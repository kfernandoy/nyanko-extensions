from __future__ import annotations

"""Infraestructura compartida por todos los motores de bundles Nyanko Source v4.

Aqui vive lo que NO es de ningun tema en concreto: el parser de HTML, los helpers
de imagen y fecha, el descifrado de paginas protegidas y la clase base con lo
minimo que toda fuente necesita (`__init__`, `_request`, `page_bytes`).

Existe para que un motor de tema NO tenga que heredar de `MadaraDetailsSource`. Antes lo
hacia: 63 de 64 motores heredaban de Madara solo para tener `_request` (5 lineas)
y el parser, y a cambio cada bundle arrastraba `madara.py` entero -- 129 KB, con
el motor Madara y hasta diez fuentes concretas ajenas dentro. Un arreglo del
motor Madara movia el sha256 de 1851 extensiones y la app las marcaba todas como
actualizables, aunque su sitio no tuviera nada que ver con Madara.

El contenido se EXTRAJO de `madara.py` sin tocarlo, para no cambiar comportamiento
al reorganizar.
"""

 

import base64 
import hashlib 
import io 
import json 
import re 
from html .parser import HTMLParser 
from typing import Any 
from urllib .parse import unquote ,urljoin ,urlparse ,urlunparse 

from PIL import Image 

from nyanko_api .sources .contract import (
SOURCE_API_VERSION ,
SourceCapabilities ,
SourceChapter ,
SourceFetcher ,
SourceFilter ,
SourcePage ,
SourcePageContent ,
SourcePreference ,
SourceSeries ,
)
from nyanko_api .sources .errors import SourceNotFoundError 

# Re-exportados para los motores de tema que se concatenan detras en el bundle:
# los usan en sus firmas y antes los tomaban del namespace de madara.py.
__all__ =[
"FuenteBaseSource",
"SourceChapter",
"SourceFilter",
"SourcePage",
"SourcePreference",
"SourceSeries",
]


def _es_no_encontrado (error :BaseException )->bool :
    """`True` si la excepcion representa un 404 de la fuente.

    No se hace `except httpx.HTTPStatusError` porque el error puede llegar de dos
    formas segun quien envuelva al fetcher: `httpx.HTTPStatusError` crudo, o el
    `SourceNotFoundError` en que lo traduce la app. Se cubren ambas sin importar
    httpx aqui, que este motor no lo trae.
    """
    if isinstance (error ,SourceNotFoundError ):
        return True 
    respuesta =getattr (error ,"response",None )
    return getattr (respuesta ,"status_code",None )==404 


class _Node :
    def __init__ (
    self ,
    tag :str ="",
    attrs :list [tuple [str ,str |None ]]|None =None ,
    parent :_Node |None =None ,
    )->None :
        self .tag =tag 
        self .attrs ={key :value or ""for key ,value in attrs or []}
        self .parent =parent 
        self .children :list [_Node |str ]=[]

    def text (self )->str :
        return " ".join (
        part 
        for child in self .children 
        if (part :=child .text ()if isinstance (child ,_Node )else child .strip ())
        )

    def descendants (self ,tag :str |None =None )->list [_Node ]:
        result :list [_Node ]=[]
        for child in self .children :
            if not isinstance (child ,_Node ):
                continue 
            if tag is None or child .tag ==tag :
                result .append (child )
            result .extend (child .descendants (tag ))
        return result 

    def has_class (self ,name :str )->bool :
        return name in self .attrs .get ("class","").split ()


class _TreeParser (HTMLParser ):
    _VOID ={"area","base","br","col","embed","hr","img","input","link","meta","source"}

    def __init__ (self )->None :
        super ().__init__ (convert_charrefs =True )
        self .root =_Node ()
        self .current =self .root 

    def handle_starttag (self ,tag :str ,attrs :list [tuple [str ,str |None ]])->None :
        node =_Node (tag ,attrs ,self .current )
        self .current .children .append (node )
        if tag not in self ._VOID :
            self .current =node 

    def handle_startendtag (self ,tag :str ,attrs :list [tuple [str ,str |None ]])->None :
        self .current .children .append (_Node (tag ,attrs ,self .current ))

    def handle_endtag (self ,tag :str )->None :
        node =self .current 
        while node .parent is not None :
            if node .tag ==tag :
                self .current =node .parent 
                return 
            node =node .parent 

    def handle_data (self ,data :str )->None :
        self .current .children .append (data )


def _parse_html (value :str )->_Node :
    parser =_TreeParser ()
    parser .feed (value )
    return parser .root 


def _first (node :_Node ,predicate :Any )->_Node |None :
    return next ((item for item in node .descendants ()if predicate (item )),None )


_BACKGROUND_IMAGE =re .compile (r"background(?:-image)?\s*:[^;]*?url\(\s*(['\"]?)(.*?)\1\s*\)",re .I |re .S )


def _style_image_url (node :_Node ,base_url :str )->str :
    """Portada servida como CSS en el propio nodo, no como <img>.

    Los temas Madara re-skineados con Tailwind pintan la portada con
    ``style="background-image:url(...)"`` sobre el ancla de la serie y no
    emiten ni un solo ``<img>``.
    """
    found =_BACKGROUND_IMAGE .search (node .attrs .get ("style",""))
    if found is None :
        return ""
    value =found .group (2 ).strip ()
    return _mismo_host_seguro (urljoin (base_url ,value ),base_url )if value else ""


def _cuerpo_de_formulario (kwargs :dict [str ,Any ])->dict [str ,Any ]:
    """Convierte ``data=[(clave, valor), ...]`` en ``dict`` antes de salir a la red.

    httpx 0.28 solo trata como formulario los ``data`` que son Mapping; una lista de pares
    la interpreta como cuerpo iterable, o sea un stream SINCRONO, y sobre el cliente async
    de la app aborta con ``RuntimeError: Attempted to send an sync request with an
    AsyncClient instance``. La fuente no llegaba a hacer ni una peticion: browse, latest y
    search morian de golpe (haremdekira, "no carga nada" en la validacion manual).

    Se normaliza aqui, en el unico embudo por el que salen todas las peticiones del motor,
    en vez de en cada helper. Las claves de estos formularios son unicas -van indexadas
    como ``vars[meta_query][0][key]``-, asi que pasar por ``dict`` no pierde nada; se
    comprobo sobre 762 combinaciones de filtros. Si alguna vez hiciera falta repetir una
    clave, habria que pasarla ya codificada como ``content=``.
    """
    cuerpo =kwargs .get ("data")
    if isinstance (cuerpo ,(list ,tuple )):
        kwargs =dict (kwargs )
        kwargs ["data"]=dict (cuerpo )
    return kwargs 


def _mismo_host_seguro (url :str ,base_url :str )->str :
    """Sube a https las URLs http:// del propio sitio cuando este ya sirve por https.

    Varios temas Madara emiten las portadas y las paginas del capitulo con el esquema
    en claro aunque el sitio se sirva por https (catharsisworld: 8 de 8 paginas y 16 de
    16 portadas). En Python da igual y por eso el arnes las descargaba sin quejarse,
    pero Android bloquea el trafico cleartext desde API 28, asi que la imagen nunca
    llegaba al lector y el capitulo se veia en blanco.

    Solo se reescribe cuando el host es exactamente el de ``base_url`` y este es https;
    los CDN de terceros se dejan intactos porque no hay garantia de que tengan
    certificado valido.
    """
    if not url .startswith ("http://")or not base_url .startswith ("https://"):
        return url 
    if urlparse (url ).netloc .lower ()!=urlparse (base_url ).netloc .lower ():
        return url 
    return "https://"+url [len ("http://"):]


def _image_url (node :_Node ,base_url :str )->str :
    for key in (
    "data-lm-orig-src",
    "data-sec-src",
    "data-src",
    "data-lazy-src",
    "data-cfsrc",
    "data-manga-src",
    "data-src-base64",
    "src",
    ):
        if node .attrs .get (key ):
            return _mismo_host_seguro (urljoin (base_url ,node .attrs [key ].strip ()),base_url )
    candidates =[
    item .strip ().split ()[0 ]
    for item in node .attrs .get ("srcset","").split (",")
    if item .strip ()
    ]
    if candidates :
        return _mismo_host_seguro (urljoin (base_url ,candidates [-1 ]),base_url )
    return _style_image_url (node ,base_url )


def _es_imagen_de_carga (node :_Node )->bool :
    """`True` si el <img> es el spinner del tema y no la portada.

    Algunos temas Madara meten un placeholder ANTES de la portada real
    (taurusfansub: `<div class="manga-loader"><img alt="Loading..."></div>` seguido de
    `<div class="manga__thumb_item"><img ...la portada...>`). Coger el primer <img> del
    contenedor devolvia el mismo spinner para las 12 series del listado.

    Se detecta por el alt y por la clase del contenedor, no por la URL: el archivo
    concreto cambia de un sitio a otro.
    """
    if "load"in node .attrs .get ("alt","").casefold ():
        return True 
    padre =node .parent 
    saltos =0 
    while padre is not None and saltos <3 :
        clases =padre .attrs .get ("class","").casefold ()
        if "loader"in clases or "loading"in clases :
            return True 
        padre =padre .parent 
        saltos +=1 
    return False 


def _cover_url (container :_Node ,base_url :str )->str |None :
    """Portada del contenedor: primero el <img>, si no el background del CSS.

    Es aditivo: el fallback de ``background-image`` solo entra cuando no hay
    ningun ``<img>`` con URL utilizable, asi que no puede cambiar el resultado
    de los sitios que hoy funcionan.
    """
    image =_first (
    container ,
    lambda node :node .tag =="img"and not _es_imagen_de_carga (node ),
    )
    if image is not None and (url :=_image_url (image ,base_url )):
        return url 
        # Si solo habia loaders, se reintenta sin el filtro antes de pasar al CSS: es
        # preferible un placeholder a quedarse sin portada.
    image =_first (container ,lambda node :node .tag =="img")
    if image is not None and (url :=_image_url (image ,base_url )):
        return url 
    if url :=_style_image_url (container ,base_url ):
        return url 
    styled =_first (container ,lambda node :bool (_style_image_url (node ,base_url )))
    return _style_image_url (styled ,base_url )if styled is not None else None 


def _gf_mul (left :int ,right :int )->int :
    result =0 
    while right :
        if right &1 :
            result ^=left 
        left =((left <<1 )^(0x11B if left &0x80 else 0 ))&0xFF 
        right >>=1 
    return result 


def _aes_sbox (value :int )->int :
    inverse ,base ,exponent =1 ,value ,254 
    while exponent :
        if exponent &1 :
            inverse =_gf_mul (inverse ,base )
        base =_gf_mul (base ,base )
        exponent >>=1 
    if value ==0 :
        inverse =0 
    return inverse ^((inverse <<1 )|(inverse >>7 ))&0xFF ^((inverse <<2 )|(inverse >>6 ))&0xFF ^((inverse <<3 )|(inverse >>5 ))&0xFF ^((inverse <<4 )|(inverse >>4 ))&0xFF ^0x63 


_AES_SBOX =tuple (_aes_sbox (value )for value in range (256 ))


_AES_INV_SBOX =tuple (_AES_SBOX .index (value )for value in range (256 ))


def _aes256_decrypt (ciphertext :bytes ,key :bytes ,iv :bytes )->bytes :
    words =[list (key [index :index +4 ])for index in range (0 ,32 ,4 )]
    rcon =1 
    for index in range (8 ,60 ):
        temp =words [-1 ][:]
        if index %8 ==0 :
            temp =[_AES_SBOX [value ]for value in temp [1 :]+temp [:1 ]]
            temp [0 ]^=rcon 
            rcon =_gf_mul (rcon ,2 )
        elif index %8 ==4 :
            temp =[_AES_SBOX [value ]for value in temp ]
        words .append ([left ^right for left ,right in zip (words [index -8 ],temp )])
    round_keys =[sum (words [index :index +4 ],[])for index in range (0 ,60 ,4 )]

    def decrypt_block (block :bytes )->bytes :
        state =[value ^key_value for value ,key_value in zip (block ,round_keys [14 ])]
        for round_number in range (13 ,-1 ,-1 ):
            state =[state [index ]for index in (0 ,13 ,10 ,7 ,4 ,1 ,14 ,11 ,8 ,5 ,2 ,15 ,12 ,9 ,6 ,3 )]
            state =[_AES_INV_SBOX [value ]for value in state ]
            state =[value ^key_value for value ,key_value in zip (state ,round_keys [round_number ])]
            if round_number :
                mixed :list [int ]=[]
                for column in range (4 ):
                    a ,b ,c ,d =state [column *4 :column *4 +4 ]
                    mixed .extend ((
                    _gf_mul (a ,14 )^_gf_mul (b ,11 )^_gf_mul (c ,13 )^_gf_mul (d ,9 ),
                    _gf_mul (a ,9 )^_gf_mul (b ,14 )^_gf_mul (c ,11 )^_gf_mul (d ,13 ),
                    _gf_mul (a ,13 )^_gf_mul (b ,9 )^_gf_mul (c ,14 )^_gf_mul (d ,11 ),
                    _gf_mul (a ,11 )^_gf_mul (b ,13 )^_gf_mul (c ,9 )^_gf_mul (d ,14 ),
                    ))
                state =mixed 
        return bytes (state )

    result =b""
    previous =iv 
    for offset in range (0 ,len (ciphertext ),16 ):
        block =ciphertext [offset :offset +16 ]
        decrypted =decrypt_block (block )
        result +=bytes (left ^right for left ,right in zip (decrypted ,previous ))
        previous =block 
    return result [:-result [-1 ]]if result else result 


def _evp_kdf_decrypt (ciphertext :str ,salt :str ,password :str ,iv :str |None =None )->str :
    """Descifra el AES-256-CBC que produce `CryptoJS.AES.encrypt` con passphrase.

    CryptoJS no usa la passphrase como clave: la pasa por EvpKDF (MD5 iterado sobre
    password+salt) hasta sacar 48 bytes, de los que los 32 primeros son la clave y los
    16 siguientes el IV. Cuando el payload trae `iv` propio se usa ese en su lugar.

    Se reimplementa AES en Python puro, igual que `generic.py`, porque los bundles se
    generan autocontenidos y no pueden arrastrar dependencias externas.
    """
    generado =b""
    digest =b""
    password_bytes =password .encode ()
    salt_bytes =bytes .fromhex (salt )
    while len (generado )<48 :
        digest =hashlib .md5 (digest +password_bytes +salt_bytes ).digest ()
        generado +=digest 
    vector =bytes .fromhex (iv )if iv else generado [32 :48 ]
    return _aes256_decrypt (base64 .b64decode (ciphertext ),generado [:32 ],vector ).decode ()


def _protected_page_urls (html :str ,base_url :str )->list [str ]:
    """Paginas del plugin `wp-manga-chapter-images-protection`.

    Ese plugin sustituye los <img> del lector por divs `.page-break` vacios y publica la
    lista real cifrada en `var chapter_data`. Sin esto la extension devuelve 0 paginas
    aunque el capitulo exista (catharsisworld: 17 imagenes por capitulo).

    La passphrase es uno de los nonces de 10 hex que WordPress ya imprime en la pagina;
    no hay forma fiable de saber cual, asi que se prueban en orden y se acepta el primero
    que produzca un JSON con lista de URLs.
    """
    payload =re .search (r"var\s+chapter_data\s*=\s*'([^']+)'",html )
    if payload is None :
        return []
    try :
        datos =json .loads (payload .group (1 ).replace ("\\/","/"))
    except json .JSONDecodeError :
        return []
    if not datos .get ("ct")or not datos .get ("s"):
        return []

    candidatas :list [str ]=[]
    for encontrado in re .finditer (r"""["']([0-9a-f]{10})["']""",html ):
        if encontrado .group (1 )not in candidatas :
            candidatas .append (encontrado .group (1 ))

    for clave in candidatas [:12 ]:
        try :
            valor =json .loads (
            _evp_kdf_decrypt (datos ["ct"],datos ["s"],clave ,datos .get ("iv"))
            )
            while isinstance (valor ,str ):
                valor =json .loads (valor )
        except (ValueError ,UnicodeDecodeError ,json .JSONDecodeError ):
            continue 
        if isinstance (valor ,list )and valor :
            return [
            urljoin (base_url ,str (item ).strip ().replace ("\\/","/"))
            for item in valor 
            if str (item ).strip ()
            ]
    return []


class FuenteBaseSource :
    """Base minima de toda fuente: identidad, capacidades y peticion HTTP.

    Deliberadamente NO trae `browse`, `search`, `details`, `chapters` ni `pages`:
    eso es trabajo de cada tema. Quien herede de aqui debe implementarlos.
    """

    name ="madara"

    display_name ="Madara"

    base_url =""

    language =""

    manga_substring ="manga"

    load_more ="auto"

    supports_latest =True 

    use_new_chapter_endpoint =False 

    chapter_url_suffix ="?style=list"

    requests_per_minute =60 

    pages_profile ="default"

    extra_headers :dict [str ,str ]={}

    image_headers :dict [str ,str ]={}

    strip_external_image_referer =False 

    date_format ="MMMM dd, yyyy"

    date_locale ="en"

    details_profile ="default"

    api_version =SOURCE_API_VERSION 

    content_warning ="unknown"

    requires_auth =False 

    _ESTADOS_BADGE ={
    "ongoing","oncoming","on going","completed","completo","completado",
    "finalizado","concluido","en curso","curso","pausado","en espera",
    "on hold","canceled","cancelado","hiatus","publicandose","en emision",
    }

    def __init__ (self ,fetcher :SourceFetcher |None =None )->None :
        self .fetcher =fetcher 
        self ._load_more_detected =self .load_more =="always"
        self .capabilities =SourceCapabilities (
        search =True ,
        browse =True ,
        headers ={
        "User-Agent":"Nyanko/0.2.4",
        "Referer":f"{self .base_url }/",
        **self .extra_headers ,
        },
        requests_per_minute =self .requests_per_minute ,
        content_warning =self .content_warning ,
        requires_auth =self .requires_auth ,
        )

    @staticmethod 
    def _madara_status (value :str )->str |None :
        normalized =" ".join (re .findall (r"\w+",value .casefold ()))
        if normalized in {"completed","completo","completado","finalizado","concluido"}:
            return "completed"
        if normalized in {
        "ongoing","en curso","curso","en marcha","publicandose","en emision",
        "emision","emisión","en emisión","ativo","updating",
        }:
            return "ongoing"
        if normalized in {"on hold","pausado","en espera"}:
            return "hiatus"
        if normalized in {"canceled","cancelado"}:
            return "cancelled"
        return None 

    def _madara_date (self ,value :str )->str |None :
        from calendar import monthrange 
        from datetime import datetime ,timedelta 

        text =value .strip ().casefold ()
        now =datetime .now ().replace (microsecond =0 )
        if text .startswith (("today","hoy")):
            return now .replace (hour =0 ,minute =0 ,second =0 ).isoformat ()
        if text .startswith (("yesterday","ayer")):
            return (now -timedelta (days =1 )).replace (hour =0 ,minute =0 ,second =0 ).isoformat ()
        relative =re .search (r"(\d+)",text )
        if relative and (text .startswith ("hace")or text .endswith (("ago","atrás"))):
            amount =int (relative .group ())
            if any (unit in text for unit in ("día","dia","day")):
                return (now -timedelta (days =amount )).isoformat ()
            if any (unit in text for unit in ("hora","hour")):
                return (now -timedelta (hours =amount )).isoformat ()
            if any (unit in text for unit in ("minuto","minute"," min")):
                return (now -timedelta (minutes =amount )).isoformat ()
            if any (unit in text for unit in ("segundo","second")):
                return (now -timedelta (seconds =amount )).isoformat ()
            if any (unit in text for unit in ("semana","week")):
                return (now -timedelta (days =amount *7 )).isoformat ()
            if any (unit in text for unit in ("mes","month")):
                total =now .year *12 +now .month -1 -amount 
                year ,month =divmod (total ,12 )
                return now .replace (
                year =year ,month =month +1 ,
                day =min (now .day ,monthrange (year ,month +1 )[1 ]),
                ).isoformat ()
            if any (unit in text for unit in ("año","year")):
                year =now .year -amount 
                return now .replace (year =year ,day =min (now .day ,monthrange (year ,now .month )[1 ])).isoformat ()
        numeric_format ={
        "MM/dd/yyyy":"%m/%d/%Y","dd/MM/yyyy":"%d/%m/%Y","yyyy-MM-dd":"%Y-%m-%d",
        }.get (self .date_format )
        if numeric_format :
            try :
                return datetime .strptime (value .strip (),numeric_format ).isoformat ()
            except ValueError :
                return None 
        if self .date_format not in {"d MMMM, yyyy","dd MMM yyyy","dd MMM, yyyy","dd MMMM, yyyy","MMM dd, yyyy","MMMM dd, yyyy"}:
            return None 
        months ={
        "january":1 ,"february":2 ,"march":3 ,"april":4 ,"may":5 ,"june":6 ,
        "july":7 ,"august":8 ,"september":9 ,"october":10 ,"november":11 ,"december":12 ,
        "enero":1 ,"febrero":2 ,"marzo":3 ,"abril":4 ,"mayo":5 ,"junio":6 ,
        "julio":7 ,"agosto":8 ,"septiembre":9 ,"octubre":10 ,"noviembre":11 ,"diciembre":12 ,
        "jan":1 ,"feb":2 ,"mar":3 ,"apr":4 ,"jun":6 ,"jul":7 ,"aug":8 ,
        "sep":9 ,"sept":9 ,"oct":10 ,"nov":11 ,"dec":12 ,
        "ene":1 ,"abr":4 ,"ago":8 ,"dic":12 ,
        }
        day_first =self .date_format .startswith (("d ","dd "))
        absolute =(
        re .fullmatch (r"(\d{1,2})\s+([^\s,]+),?\s+(\d{4})",text )
        if day_first 
        else re .fullmatch (r"([^\s]+)\s+(\d{1,2}),\s*(\d{4})",text )
        )
        month =absolute .group (2 ).rstrip (".")if absolute and day_first else absolute .group (1 ).rstrip (".")if absolute else ""
        if absolute and month in months :
            day =absolute .group (1 )if day_first else absolute .group (2 )
            return datetime (int (absolute .group (3 )),months [month ],int (day )).isoformat ()
        return None 

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        url =page .source_id if isinstance (page ,SourcePage )else page 
        if not url :
            raise SourceNotFoundError ("Página Madara sin URL")
        parsed =urlparse (url )
        headers =dict (self .image_headers )
        if isinstance (page ,SourcePage )and not (
        self .strip_external_image_referer 
        and parsed .hostname !=urlparse (self .base_url ).hostname 
        ):
            headers .setdefault ("Referer",page .chapter_id )
        response =await self ._request (
        "GET",
        urlunparse (parsed ._replace (fragment ="")),
        headers =headers ,
        )
        response .raise_for_status ()
        content =response .content 
        if parsed .fragment and self .pages_profile =="scrambled":
            data =json .loads (unquote (parsed .fragment ))
            source =Image .open (io .BytesIO (content )).convert ("RGBA")
            output =Image .new ("RGBA",source .size )
            width ,height =int (data ["blockWidth"]),int (data ["blockHeight"])
            for dest_x ,dest_y ,src_x ,src_y ,*_ in data ["matrix"]:
                block =source .crop ((int (src_x ),int (src_y ),int (src_x )+width ,int (src_y )+height ))
                output .paste (block ,(int (dest_x ),int (dest_y )))
            buffer =io .BytesIO ()
            output .convert ("RGB").save (buffer ,"JPEG",quality =90 )
            content =buffer .getvalue ()
        return SourcePageContent (
        media_type ="image/jpeg"if parsed .fragment else response .headers .get ("Content-Type","image/jpeg"),
        chunks =iter ([content ]),
        )

    @staticmethod 
    def _has_class_ancestor (node :_Node ,class_name :str )->bool :
        parent =node .parent 
        while parent is not None :
            if parent .has_class (class_name ):
                return True 
            parent =parent .parent 
        return False 

    @staticmethod 
    def _has_id_ancestor (node :_Node ,identifier :str )->bool :
        parent =node .parent 
        while parent is not None :
            if parent .attrs .get ("id")==identifier :
                return True 
            parent =parent .parent 
        return False 

    async def _request (self ,method :str ,url :str ,**kwargs :Any )->Any :
        if self .fetcher is None :
            raise SourceNotFoundError (f"{self .display_name } no tiene fetcher inyectado")
        kwargs =_cuerpo_de_formulario (kwargs )
        return await self .fetcher .request (method ,url ,**kwargs )

"""Ficha HTML compartida por Madara y temas con marcado compatible."""

import re 
from urllib .parse import urljoin 



class MadaraDetailsSource (FuenteBaseSource ):
    """Añade solo la ficha; cada motor conserva su catalogo, capitulos y lector."""

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        title_node =_first (
        root ,
        lambda node :node .tag in {"h1","h3"}
        and (
        self ._has_class_ancestor (node ,"post-title")
        or self ._has_id_ancestor (node ,"manga-title")
        or node .has_class ("post-title")
        or node .has_class ("mb-2")
        ),
        )
        title =title_node .text ().strip ()if title_node else (
        series .title if isinstance (series ,SourceSeries )else series_id .rstrip ("/").rsplit ("/",1 )[-1 ]
        )
        image =_first (root ,lambda node :node .tag =="img"and self ._has_class_ancestor (node ,"summary_image"))
        description_node =_first (
        root ,
        lambda node :node .has_class ("summary__content")
        and self ._has_class_ancestor (node ,"description-summary")
        or node .has_class ("manga-excerpt")
        or node .has_class ("mv-synopsis")
        or node .has_class ("summary-container")
        or node .has_class ("modal-contenido")and self ._has_class_ancestor (node ,"c-page__content"),
        )
        paragraphs =description_node .descendants ("p")if description_node else []
        description =(
        "\n\n".join (paragraph .text ().strip ()for paragraph in paragraphs if paragraph .text ().strip ())
        if paragraphs else description_node .text ().strip ()if description_node else ""
        )
        authors =self ._detail_links (root ,("author-content","manga-authors"))
        artists =self ._detail_links (root ,("artist-content",))
        status_text =""
        for item in root .descendants ("div"):
            if not item .has_class ("post-content_item")or not self ._has_class_ancestor (item ,"summary_content"):
                continue 
            heading =_first (
            item ,
            lambda node :node .has_class ("summary-heading")
            and any (label in node .text ().casefold ()for label in ("status","estado")),
            )
            value =_first (item ,lambda node :node .has_class ("summary-content"))
            if heading and value :
                status_text =value .text ().strip ()
        genres =[
        node .text ().strip ()
        for node in root .descendants ("a")
        if self ._has_class_ancestor (node ,"genres-content")and node .text ().strip ()
        ]
        for item in root .descendants ():
            if not item .has_class ("post-content_item"):
                continue 
            own =" ".join (child .strip ()for child in item .children if isinstance (child ,str )and child .strip ())
            heading =_first (item ,lambda node :node .has_class ("summary-heading"))
            label =f"{own } {heading .text ()if heading else ''}"
            value =_first (item ,lambda node :node .has_class ("summary-content"))
            if not value or not value .text ().strip ():
                continue 
            if "Type"in label and value .text ().strip ()!="-":
                genres .append (value .text ().strip ())
            elif "Alt"in label :
                description =f"{description }\n\nAlternative name(s): {value .text ().strip ()}".strip ()
        genres =list (dict .fromkeys (genre for genre in genres if genre ))
        cover_url =_image_url (image ,str (response .url ))if image else None 
        if not (cover_url and description and genres and status_text ):
            alternative =self ._tailwind_details (root ,str (response .url ))
            slug =series_id .rstrip ("/").rsplit ("/",1 )[-1 ]
            title =title if title and title !=slug else alternative .get ("title")or title 
            cover_url =cover_url or alternative .get ("cover_url")
            description =description or alternative .get ("description","")
            status_text =status_text or alternative .get ("status","")
            if not genres :
                genres =alternative .get ("genres",[])
            if not authors and alternative .get ("author"):
                authors =[alternative ["author"]]
        return SourceSeries (
        source_id =series_id ,
        title =title ,
        source_name =self .name ,
        cover_url =cover_url ,
        description =description or None ,
        author =", ".join (authors )or None ,
        artist =", ".join (artists )or None ,
        status =self ._madara_status (status_text ),
        content_tags =tuple (genres ),
        metadata =series .metadata if isinstance (series ,SourceSeries )else {},
        web_url =str (response .url ),
        )

    def _tailwind_details (self ,root :_Node ,page_url :str )->dict :
        data :dict ={}
        title =_first (root ,lambda node :node .tag =="h1")
        if title and title .text ().strip ():
            data ["title"]=title .text ().strip ()
        synopsis =_first (root ,lambda node :node .attrs .get ("id")=="expand_content")
        if synopsis and synopsis .text ().strip ():
            data ["description"]=synopsis .text ().strip ()
        for node in root .descendants ("div"):
            if any ("0.75/1"in value for value in node .attrs .get ("class","").split ()):
                if url :=_style_image_url (node ,page_url ):
                    data ["cover_url"]=url 
                    break 
        genres :list [str ]=[]
        seen :set [str ]=set ()
        for node in root .descendants ():
            if node .attrs .get ("id")=="expand_content":
                break 
            if not any ("rounded"in value for value in node .attrs .get ("class","").split ()):
                continue 
            text =node .text ().strip ()
            if not text or len (text )>=80 :
                continue 
            key =text .casefold ()
            if key in self ._ESTADOS_BADGE :
                data .setdefault ("status",text )
            elif key not in seen :
                seen .add (key )
                genres .append (text )
        if genres :
            data ["genres"]=genres 
        ld_json =_first (
        root ,
        lambda node :node .tag =="script"and node .attrs .get ("type")=="application/ld+json",
        )
        if ld_json is not None :
            author =re .search (r'"author"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"',ld_json .text ())
            if author :
                data ["author"]=author .group (1 ).strip ()
        return data 

    @classmethod 
    def _detail_links (cls ,root :_Node ,containers :tuple [str ,...])->list [str ]:
        return [
        node .text ().strip ()
        for node in root .descendants ("a")
        if any (cls ._has_class_ancestor (node ,name )for name in containers )
        and node .text ().strip ()
        and "updating"not in node .text ().casefold ()
        and "atualizando"not in node .text ().casefold ()
        ]

"""Implementación común del tema Madara para bundles Nyanko Source v4."""

 

import ast 
import base64 
import hashlib 
import io 
import json 
import re 
from html .parser import HTMLParser 
from typing import Any 
from urllib .parse import parse_qs ,unquote ,urlencode ,urljoin ,urlparse ,urlunparse 

from PIL import Image 

from nyanko_api .sources .contract import (
SOURCE_API_VERSION ,
SourceCapabilities ,
SourceChapter ,
SourceFetcher ,
SourceFilter ,
SourcePage ,
SourcePageContent ,
SourcePreference ,
SourceSeries ,
)
from nyanko_api .sources .errors import SourceNotFoundError 


def _es_no_encontrado (error :BaseException )->bool :
    """`True` si la excepcion representa un 404 de la fuente.

    No se hace `except httpx.HTTPStatusError` porque el error puede llegar de dos
    formas segun quien envuelva al fetcher: `httpx.HTTPStatusError` crudo, o el
    `SourceNotFoundError` en que lo traduce la app. Se cubren ambas sin importar
    httpx aqui, que este motor no lo trae.
    """
    if isinstance (error ,SourceNotFoundError ):
        return True 
    respuesta =getattr (error ,"response",None )
    return getattr (respuesta ,"status_code",None )==404 


class _Node :
    def __init__ (
    self ,
    tag :str ="",
    attrs :list [tuple [str ,str |None ]]|None =None ,
    parent :_Node |None =None ,
    )->None :
        self .tag =tag 
        self .attrs ={key :value or ""for key ,value in attrs or []}
        self .parent =parent 
        self .children :list [_Node |str ]=[]

    def text (self )->str :
        return " ".join (
        part 
        for child in self .children 
        if (part :=child .text ()if isinstance (child ,_Node )else child .strip ())
        )

    def descendants (self ,tag :str |None =None )->list [_Node ]:
        result :list [_Node ]=[]
        for child in self .children :
            if not isinstance (child ,_Node ):
                continue 
            if tag is None or child .tag ==tag :
                result .append (child )
            result .extend (child .descendants (tag ))
        return result 

    def has_class (self ,name :str )->bool :
        return name in self .attrs .get ("class","").split ()


class _TreeParser (HTMLParser ):
    _VOID ={"area","base","br","col","embed","hr","img","input","link","meta","source"}

    def __init__ (self )->None :
        super ().__init__ (convert_charrefs =True )
        self .root =_Node ()
        self .current =self .root 

    def handle_starttag (self ,tag :str ,attrs :list [tuple [str ,str |None ]])->None :
        node =_Node (tag ,attrs ,self .current )
        self .current .children .append (node )
        if tag not in self ._VOID :
            self .current =node 

    def handle_startendtag (self ,tag :str ,attrs :list [tuple [str ,str |None ]])->None :
        self .current .children .append (_Node (tag ,attrs ,self .current ))

    def handle_endtag (self ,tag :str )->None :
        node =self .current 
        while node .parent is not None :
            if node .tag ==tag :
                self .current =node .parent 
                return 
            node =node .parent 

    def handle_data (self ,data :str )->None :
        self .current .children .append (data )


def _parse_html (value :str )->_Node :
    parser =_TreeParser ()
    parser .feed (value )
    return parser .root 


def _first (node :_Node ,predicate :Any )->_Node |None :
    return next ((item for item in node .descendants ()if predicate (item )),None )


_BACKGROUND_IMAGE =re .compile (r"background(?:-image)?\s*:[^;]*?url\(\s*(['\"]?)(.*?)\1\s*\)",re .I |re .S )


def _style_image_url (node :_Node ,base_url :str )->str :
    """Portada servida como CSS en el propio nodo, no como <img>.

    Los temas Madara re-skineados con Tailwind pintan la portada con
    ``style="background-image:url(...)"`` sobre el ancla de la serie y no
    emiten ni un solo ``<img>``.
    """
    found =_BACKGROUND_IMAGE .search (node .attrs .get ("style",""))
    if found is None :
        return ""
    value =found .group (2 ).strip ()
    return _mismo_host_seguro (urljoin (base_url ,value ),base_url )if value else ""


def _cuerpo_de_formulario (kwargs :dict [str ,Any ])->dict [str ,Any ]:
    """Convierte ``data=[(clave, valor), ...]`` en ``dict`` antes de salir a la red.

    httpx 0.28 solo trata como formulario los ``data`` que son Mapping; una lista de pares
    la interpreta como cuerpo iterable, o sea un stream SINCRONO, y sobre el cliente async
    de la app aborta con ``RuntimeError: Attempted to send an sync request with an
    AsyncClient instance``. La fuente no llegaba a hacer ni una peticion: browse, latest y
    search morian de golpe (haremdekira, "no carga nada" en la validacion manual).

    Se normaliza aqui, en el unico embudo por el que salen todas las peticiones del motor,
    en vez de en cada helper. Las claves de estos formularios son unicas -van indexadas
    como ``vars[meta_query][0][key]``-, asi que pasar por ``dict`` no pierde nada; se
    comprobo sobre 762 combinaciones de filtros. Si alguna vez hiciera falta repetir una
    clave, habria que pasarla ya codificada como ``content=``.
    """
    cuerpo =kwargs .get ("data")
    if isinstance (cuerpo ,(list ,tuple )):
        kwargs =dict (kwargs )
        kwargs ["data"]=dict (cuerpo )
    return kwargs 


def _mismo_host_seguro (url :str ,base_url :str )->str :
    """Sube a https las URLs http:// del propio sitio cuando este ya sirve por https.

    Varios temas Madara emiten las portadas y las paginas del capitulo con el esquema
    en claro aunque el sitio se sirva por https (catharsisworld: 8 de 8 paginas y 16 de
    16 portadas). En Python da igual y por eso el arnes las descargaba sin quejarse,
    pero Android bloquea el trafico cleartext desde API 28, asi que la imagen nunca
    llegaba al lector y el capitulo se veia en blanco.

    Solo se reescribe cuando el host es exactamente el de ``base_url`` y este es https;
    los CDN de terceros se dejan intactos porque no hay garantia de que tengan
    certificado valido.
    """
    if not url .startswith ("http://")or not base_url .startswith ("https://"):
        return url 
    if urlparse (url ).netloc .lower ()!=urlparse (base_url ).netloc .lower ():
        return url 
    return "https://"+url [len ("http://"):]


def _image_url (node :_Node ,base_url :str )->str :
    for key in (
    "data-lm-orig-src",
    "data-sec-src",
    "data-src",
    "data-lazy-src",
    "data-cfsrc",
    "data-manga-src",
    "data-src-base64",
    "src",
    ):
        if node .attrs .get (key ):
            return _mismo_host_seguro (urljoin (base_url ,node .attrs [key ].strip ()),base_url )
    candidates =[
    item .strip ().split ()[0 ]
    for item in node .attrs .get ("srcset","").split (",")
    if item .strip ()
    ]
    if candidates :
        return _mismo_host_seguro (urljoin (base_url ,candidates [-1 ]),base_url )
    return _style_image_url (node ,base_url )


def _es_imagen_de_carga (node :_Node )->bool :
    """`True` si el <img> es el spinner del tema y no la portada.

    Algunos temas Madara meten un placeholder ANTES de la portada real
    (taurusfansub: `<div class="manga-loader"><img alt="Loading..."></div>` seguido de
    `<div class="manga__thumb_item"><img ...la portada...>`). Coger el primer <img> del
    contenedor devolvia el mismo spinner para las 12 series del listado.

    Se detecta por el alt y por la clase del contenedor, no por la URL: el archivo
    concreto cambia de un sitio a otro.
    """
    if "load"in node .attrs .get ("alt","").casefold ():
        return True 
    padre =node .parent 
    saltos =0 
    while padre is not None and saltos <3 :
        clases =padre .attrs .get ("class","").casefold ()
        if "loader"in clases or "loading"in clases :
            return True 
        padre =padre .parent 
        saltos +=1 
    return False 


def _cover_url (container :_Node ,base_url :str )->str |None :
    """Portada del contenedor: primero el <img>, si no el background del CSS.

    Es aditivo: el fallback de ``background-image`` solo entra cuando no hay
    ningun ``<img>`` con URL utilizable, asi que no puede cambiar el resultado
    de los sitios que hoy funcionan.
    """
    image =_first (
    container ,
    lambda node :node .tag =="img"and not _es_imagen_de_carga (node ),
    )
    if image is not None and (url :=_image_url (image ,base_url )):
        return url 
        # Si solo habia loaders, se reintenta sin el filtro antes de pasar al CSS: es
        # preferible un placeholder a quedarse sin portada.
    image =_first (container ,lambda node :node .tag =="img")
    if image is not None and (url :=_image_url (image ,base_url )):
        return url 
    if url :=_style_image_url (container ,base_url ):
        return url 
    styled =_first (container ,lambda node :bool (_style_image_url (node ,base_url )))
    return _style_image_url (styled ,base_url )if styled is not None else None 


def _gf_mul (left :int ,right :int )->int :
    result =0 
    while right :
        if right &1 :
            result ^=left 
        left =((left <<1 )^(0x11B if left &0x80 else 0 ))&0xFF 
        right >>=1 
    return result 


def _aes_sbox (value :int )->int :
    inverse ,base ,exponent =1 ,value ,254 
    while exponent :
        if exponent &1 :
            inverse =_gf_mul (inverse ,base )
        base =_gf_mul (base ,base )
        exponent >>=1 
    if value ==0 :
        inverse =0 
    return inverse ^((inverse <<1 )|(inverse >>7 ))&0xFF ^((inverse <<2 )|(inverse >>6 ))&0xFF ^((inverse <<3 )|(inverse >>5 ))&0xFF ^((inverse <<4 )|(inverse >>4 ))&0xFF ^0x63 


_AES_SBOX =tuple (_aes_sbox (value )for value in range (256 ))
_AES_INV_SBOX =tuple (_AES_SBOX .index (value )for value in range (256 ))


def _aes256_decrypt (ciphertext :bytes ,key :bytes ,iv :bytes )->bytes :
    words =[list (key [index :index +4 ])for index in range (0 ,32 ,4 )]
    rcon =1 
    for index in range (8 ,60 ):
        temp =words [-1 ][:]
        if index %8 ==0 :
            temp =[_AES_SBOX [value ]for value in temp [1 :]+temp [:1 ]]
            temp [0 ]^=rcon 
            rcon =_gf_mul (rcon ,2 )
        elif index %8 ==4 :
            temp =[_AES_SBOX [value ]for value in temp ]
        words .append ([left ^right for left ,right in zip (words [index -8 ],temp )])
    round_keys =[sum (words [index :index +4 ],[])for index in range (0 ,60 ,4 )]

    def decrypt_block (block :bytes )->bytes :
        state =[value ^key_value for value ,key_value in zip (block ,round_keys [14 ])]
        for round_number in range (13 ,-1 ,-1 ):
            state =[state [index ]for index in (0 ,13 ,10 ,7 ,4 ,1 ,14 ,11 ,8 ,5 ,2 ,15 ,12 ,9 ,6 ,3 )]
            state =[_AES_INV_SBOX [value ]for value in state ]
            state =[value ^key_value for value ,key_value in zip (state ,round_keys [round_number ])]
            if round_number :
                mixed :list [int ]=[]
                for column in range (4 ):
                    a ,b ,c ,d =state [column *4 :column *4 +4 ]
                    mixed .extend ((
                    _gf_mul (a ,14 )^_gf_mul (b ,11 )^_gf_mul (c ,13 )^_gf_mul (d ,9 ),
                    _gf_mul (a ,9 )^_gf_mul (b ,14 )^_gf_mul (c ,11 )^_gf_mul (d ,13 ),
                    _gf_mul (a ,13 )^_gf_mul (b ,9 )^_gf_mul (c ,14 )^_gf_mul (d ,11 ),
                    _gf_mul (a ,11 )^_gf_mul (b ,13 )^_gf_mul (c ,9 )^_gf_mul (d ,14 ),
                    ))
                state =mixed 
        return bytes (state )

    result =b""
    previous =iv 
    for offset in range (0 ,len (ciphertext ),16 ):
        block =ciphertext [offset :offset +16 ]
        decrypted =decrypt_block (block )
        result +=bytes (left ^right for left ,right in zip (decrypted ,previous ))
        previous =block 
    return result [:-result [-1 ]]if result else result 


def _evp_kdf_decrypt (ciphertext :str ,salt :str ,password :str ,iv :str |None =None )->str :
    """Descifra el AES-256-CBC que produce `CryptoJS.AES.encrypt` con passphrase.

    CryptoJS no usa la passphrase como clave: la pasa por EvpKDF (MD5 iterado sobre
    password+salt) hasta sacar 48 bytes, de los que los 32 primeros son la clave y los
    16 siguientes el IV. Cuando el payload trae `iv` propio se usa ese en su lugar.

    Se reimplementa AES en Python puro, igual que `generic.py`, porque los bundles se
    generan autocontenidos y no pueden arrastrar dependencias externas.
    """
    generado =b""
    digest =b""
    password_bytes =password .encode ()
    salt_bytes =bytes .fromhex (salt )
    while len (generado )<48 :
        digest =hashlib .md5 (digest +password_bytes +salt_bytes ).digest ()
        generado +=digest 
    vector =bytes .fromhex (iv )if iv else generado [32 :48 ]
    return _aes256_decrypt (base64 .b64decode (ciphertext ),generado [:32 ],vector ).decode ()


def _protected_page_urls (html :str ,base_url :str )->list [str ]:
    """Paginas del plugin `wp-manga-chapter-images-protection`.

    Ese plugin sustituye los <img> del lector por divs `.page-break` vacios y publica la
    lista real cifrada en `var chapter_data`. Sin esto la extension devuelve 0 paginas
    aunque el capitulo exista (catharsisworld: 17 imagenes por capitulo).

    La passphrase es uno de los nonces de 10 hex que WordPress ya imprime en la pagina;
    no hay forma fiable de saber cual, asi que se prueban en orden y se acepta el primero
    que produzca un JSON con lista de URLs.
    """
    payload =re .search (r"var\s+chapter_data\s*=\s*'([^']+)'",html )
    if payload is None :
        return []
    try :
        datos =json .loads (payload .group (1 ).replace ("\\/","/"))
    except json .JSONDecodeError :
        return []
    if not datos .get ("ct")or not datos .get ("s"):
        return []

    candidatas :list [str ]=[]
    for encontrado in re .finditer (r"""["']([0-9a-f]{10})["']""",html ):
        if encontrado .group (1 )not in candidatas :
            candidatas .append (encontrado .group (1 ))

    for clave in candidatas [:12 ]:
        try :
            valor =json .loads (
            _evp_kdf_decrypt (datos ["ct"],datos ["s"],clave ,datos .get ("iv"))
            )
            while isinstance (valor ,str ):
                valor =json .loads (valor )
        except (ValueError ,UnicodeDecodeError ,json .JSONDecodeError ):
            continue 
        if isinstance (valor ,list )and valor :
            return [
            urljoin (base_url ,str (item ).strip ().replace ("\\/","/"))
            for item in valor 
            if str (item ).strip ()
            ]
    return []


class MadaraDetailsSource (MadaraDetailsSource ):
    name ="madara"
    display_name ="Madara"
    base_url =""
    language =""
    manga_substring ="manga"
    load_more ="auto"
    supports_latest =True 
    use_new_chapter_endpoint =False 
    chapter_url_suffix ="?style=list"
    requests_per_minute =60 
    pages_profile ="default"
    extra_headers :dict [str ,str ]={}
    image_headers :dict [str ,str ]={}
    strip_external_image_referer =False 
    date_format ="MMMM dd, yyyy"
    date_locale ="en"
    details_profile ="default"
    api_version =SOURCE_API_VERSION 
    content_warning ="unknown"
    requires_auth =False 

    def __init__ (self ,fetcher :SourceFetcher |None =None )->None :
        self .fetcher =fetcher 
        self ._load_more_detected =self .load_more =="always"
        self .capabilities =SourceCapabilities (
        search =True ,
        browse =True ,
        headers ={
        "User-Agent":"Nyanko/0.2.4",
        "Referer":f"{self .base_url }/",
        **self .extra_headers ,
        },
        requests_per_minute =self .requests_per_minute ,
        content_warning =self .content_warning ,
        requires_auth =self .requires_auth ,
        )

    async def search (self ,query :str ,limit :int =20 )->list [SourceSeries ]:
        response =await self ._request (
        "GET",
        f"{self .base_url }/",
        params ={"s":query .strip (),"post_type":"wp-manga"},
        )
        response .raise_for_status ()
        return self ._series (response .text ,("c-tabs-item__content","manga__item"))[:limit ]

    async def browse (self ,kind :str ,page :int =1 )->list [SourceSeries ]:
        if kind =="latest"and not self .supports_latest :
            return []
        if kind not in {"popular","latest"}:
            return []
        if self .load_more =="always"or (self .load_more =="auto"and self ._load_more_detected ):
            response =await self ._request (
            "POST",
            f"{self .base_url }/wp-admin/admin-ajax.php",
            data ={
            "action":"madara_load_more",
            "page":str (max (page -1 ,0 )),
            "template":"madara-core/content/content-archive",
            "vars[paged]":"1",
            "vars[post_type]":"wp-manga",
            "vars[post_status]":"publish",
            "vars[meta_key]":"_wp_manga_views"if kind =="popular"else "_latest_update",
            "vars[orderby]":"meta_value_num",
            "vars[order]":"desc",
            "vars[manga_archives_item_layout]":"big_thumbnail",
            },
            )
        else :
            suffix =""if page ==1 else f"page/{page }/"
            # En WordPress, pedir `page/N/` mas alla de la ultima devuelve 404: ese ES
            # el marcador de fin de catalogo, no un fallo. Propagarlo hacia arriba
            # reventaba la fuente al hacer scroll (infrafandub tiene 18 series en una
            # sola pagina y crasheaba a los ~2 s con "no se encontro el recurso").
            # Se devuelve vacio, y el adaptador v4 lo convierte en has_more=False.
            #
            # Se captura la EXCEPCION en vez de mirar `response.status_code`: el fetcher
            # real de la app (`RateLimitedClient`) ya llama a `raise_for_status()` dentro
            # de `request`, asi que el 404 nunca vuelve como respuesta y la comprobacion
            # por codigo no llegaba a ejecutarse nunca en produccion.
            #
            # Solo aplica a partir de la pagina 2: un 404 en la primera si es un fallo
            # real de la fuente y debe seguir viajando.
            try :
                response =await self ._request (
                "GET",
                f"{self .base_url }/{self .manga_substring .strip ('/')}/{suffix }",
                params ={"m_orderby":"views"if kind =="popular"else "latest"},
                )
            except Exception as error :
                if page >1 and _es_no_encontrado (error ):
                    return []
                raise 
            if page >1 and getattr (response ,"status_code",None )==404 :
                return []
        response .raise_for_status ()
        root =_parse_html (response .text )
        if self .load_more =="auto":
            self ._load_more_detected =any (
            node .tag =="nav"and node .has_class ("navigation-ajax")
            for node in root .descendants ()
            )
        return self ._series_from_root (root ,("page-item-detail","manga__item"))

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        title_node =_first (
        root ,
        lambda node :node .tag in {"h1","h3"}
        and (
        self ._has_class_ancestor (node ,"post-title")
        or self ._has_id_ancestor (node ,"manga-title")
        or node .has_class ("post-title")
        or node .has_class ("mb-2")
        ),
        )
        title =title_node .text ().strip ()if title_node else (
        series .title if isinstance (series ,SourceSeries )else series_id .rstrip ("/").rsplit ("/",1 )[-1 ]
        )
        image =_first (root ,lambda node :node .tag =="img"and self ._has_class_ancestor (node ,"summary_image"))
        description_node =_first (
        root ,
        lambda node :node .has_class ("summary__content")
        and self ._has_class_ancestor (node ,"description-summary")
        or node .has_class ("manga-excerpt")
        or node .has_class ("mv-synopsis")
        or node .has_class ("summary-container")
        or node .has_class ("modal-contenido")and self ._has_class_ancestor (node ,"c-page__content"),
        )
        paragraphs =description_node .descendants ("p")if description_node else []
        description =(
        "\n\n".join (paragraph .text ().strip ()for paragraph in paragraphs if paragraph .text ().strip ())
        if paragraphs else description_node .text ().strip ()if description_node else ""
        )
        authors =self ._detail_links (root ,("author-content","manga-authors"))
        artists =self ._detail_links (root ,("artist-content",))
        status_text =""
        for item in root .descendants ("div"):
            if not item .has_class ("post-content_item")or not self ._has_class_ancestor (item ,"summary_content"):
                continue 
            heading =_first (
            item ,
            lambda node :node .has_class ("summary-heading")
            and any (label in node .text ().casefold ()for label in ("status","estado")),
            )
            value =_first (item ,lambda node :node .has_class ("summary-content"))
            if heading and value :
                status_text =value .text ().strip ()
        genres =[
        node .text ().strip ()
        for node in root .descendants ("a")
        if self ._has_class_ancestor (node ,"genres-content")and node .text ().strip ()
        ]
        for item in root .descendants ():
            if not item .has_class ("post-content_item"):
                continue 
            own =" ".join (child .strip ()for child in item .children if isinstance (child ,str )and child .strip ())
            heading =_first (item ,lambda node :node .has_class ("summary-heading"))
            label =f"{own } {heading .text ()if heading else ''}"
            value =_first (item ,lambda node :node .has_class ("summary-content"))
            if not value or not value .text ().strip ():
                continue 
            if "Type"in label and value .text ().strip ()!="-":
                genres .append (value .text ().strip ())
            elif "Alt"in label :
                description =f"{description }\n\nAlternative name(s): {value .text ().strip ()}".strip ()
        genres =list (dict .fromkeys (genre for genre in genres if genre ))
        cover_url =_image_url (image ,str (response .url ))if image else None 

        # Los temas Madara re-skineados con Tailwind (catharsisworld, templescanesp) no
        # emiten NINGUNA de las clases anteriores: ni `post-title`, ni `summary_image`, ni
        # `post-content_item`. La ficha quedaba entera a None aunque el HTML tuviera todo.
        # Se rellena solo lo que falta, asi que un sitio que ya funciona no cambia.
        if not (cover_url and description and genres and status_text ):
            alterno =self ._tailwind_details (root ,str (response .url ))
            title =title if title and title !=series_id .rstrip ("/").rsplit ("/",1 )[-1 ]else alterno .get ("title")or title 
            cover_url =cover_url or alterno .get ("cover_url")
            description =description or alterno .get ("description","")
            status_text =status_text or alterno .get ("status","")
            if not genres :
                genres =alterno .get ("genres",[])
            if not authors and alterno .get ("author"):
                authors =[alterno ["author"]]

        return SourceSeries (
        source_id =series_id ,
        title =title ,
        source_name =self .name ,
        cover_url =cover_url ,
        description =description or None ,
        author =", ".join (authors )or None ,
        artist =", ".join (artists )or None ,
        status =self ._madara_status (status_text ),
        content_tags =tuple (genres ),
        metadata =series .metadata if isinstance (series ,SourceSeries )else {},
        web_url =str (response .url ),
        )

        # Estados que el tema Tailwind pinta como una badge mas, mezclada con los generos.
    _ESTADOS_BADGE ={
    "ongoing","oncoming","on going","completed","completo","completado",
    "finalizado","concluido","en curso","curso","pausado","en espera",
    "on hold","canceled","cancelado","hiatus","publicandose","en emision",
    }

    def _tailwind_details (self ,root :_Node ,page_url :str )->dict :
        """Lee la ficha de los temas Madara re-skineados con Tailwind.

        No hay clases semanticas donde agarrarse, asi que se usan las anclas estables que
        si tiene el markup: el unico ``<h1>`` es el titulo, la sinopsis vive en
        ``#expand_content``, la portada es el primer ``background-image`` con proporcion de
        poster y las badges de texto corto son estado + generos.
        """
        datos :dict ={}

        titulo =_first (root ,lambda node :node .tag =="h1")
        if titulo and titulo .text ().strip ():
            datos ["title"]=titulo .text ().strip ()

        sinopsis =_first (root ,lambda node :node .attrs .get ("id")=="expand_content")
        if sinopsis and sinopsis .text ().strip ():
            datos ["description"]=sinopsis .text ().strip ()

            # La portada de estos temas es un div con `aspect-[0.75/1]` (proporcion de poster);
            # el resto de background-image de la pagina son el fondo difuminado y los banners.
        for node in root .descendants ("div"):
            if not any ("0.75/1"in clase for clase in node .attrs .get ("class","").split ()):
                continue 
            if url :=_style_image_url (node ,page_url ):
                datos ["cover_url"]=url 
                break 

                # Las badges de la serie (estado + generos) van entre el <h1> y la sinopsis. Se
                # recorre en orden de documento y se corta al llegar a `#expand_content`: mas abajo
                # la pagina repite badges de series relacionadas y del scanlator, que antes se
                # colaban como generos ("Bloodkami Scan").
        generos :list [str ]=[]
        vistos :set [str ]=set ()
        for node in root .descendants ():
            if node .attrs .get ("id")=="expand_content":
                break 
            texto =node .text ().strip ()
            # Las badges son etiquetas cortas; el filtro de longitud evita tragarse
            # parrafos de la pagina que tambien usan <span>.
            if node .tag !="span"or not node .has_class ("capitalize")or not texto or len (texto )>40 :
                continue 
            clave =texto .casefold ()
            if clave in self ._ESTADOS_BADGE :
                datos .setdefault ("status",texto )
            elif clave not in vistos :
            # Dedupe insensible a mayusculas: el tema repite "Shoujo" y "shoujo".
                vistos .add (clave )
                generos .append (texto )
        if generos :
            datos ["genres"]=generos 

            # El ld+json es la unica fuente fiable del autor en este tema.
        ld =_first (
        root ,
        lambda node :node .tag =="script"
        and node .attrs .get ("type")=="application/ld+json",
        )
        if ld is not None :
            autor =re .search (r'"author"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]+)"',ld .text ())
            if autor :
                datos ["author"]=autor .group (1 ).strip ()
        return datos 

    @classmethod 
    def _detail_links (cls ,root :_Node ,containers :tuple [str ,...])->list [str ]:
        return [
        node .text ().strip ()
        for node in root .descendants ("a")
        if any (cls ._has_class_ancestor (node ,name )for name in containers )
        and node .text ().strip ()
        and "updating"not in node .text ().casefold ()
        and "atualizando"not in node .text ().casefold ()
        ]

    @staticmethod 
    def _madara_status (value :str )->str |None :
        normalized =" ".join (re .findall (r"\w+",value .casefold ()))
        if normalized in {"completed","completo","completado","finalizado","concluido"}:
            return "completed"
        if normalized in {
        "ongoing","en curso","curso","en marcha","publicandose","en emision",
        "emision","emisión","en emisión","ativo","updating",
        }:
            return "ongoing"
        if normalized in {"on hold","pausado","en espera"}:
            return "hiatus"
        if normalized in {"canceled","cancelado"}:
            return "cancelled"
        return None 

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else series 
        series_url =urljoin (f"{self .base_url }/",series_id )
        response =await self ._request ("GET",series_url )
        response .raise_for_status ()
        root =_parse_html (response .text )
        items =self ._chapter_nodes (root )
        if not items :
            items =self ._fallback_chapter_nodes (root )
        holder =_first (root ,lambda node :node .attrs .get ("id","").startswith ("manga-chapters-holder"))
        # El endpoint AJAX se intentaba solo si el HTML traia `manga-chapters-holder`.
        # Los temas Madara recientes ya no emiten ese div, asi que la peticion no llegaba
        # a hacerse y la serie quedaba con 0 capitulos aunque `ajax/chapters` respondiera
        # (infrafandub: 692 capitulos). El holder solo hace falta para leer su `data-id`
        # en la variante admin-ajax; para el resto basta con no tener capitulos en el HTML.
        if not items :
            if self .use_new_chapter_endpoint or holder is None :
                response =await self ._request (
                "POST",f"{series_url .rstrip ('/')}/ajax/chapters",
                headers ={"X-Requested-With":"XMLHttpRequest"},
                )
            else :
                response =await self ._request (
                "POST",
                f"{self .base_url }/wp-admin/admin-ajax.php",
                data ={"action":"manga_get_chapters","manga":holder .attrs .get ("data-id","")},
                headers ={"X-Requested-With":"XMLHttpRequest"},
                )
                if getattr (response ,"status_code",200 )==400 :
                    response =await self ._request (
                    "POST",f"{series_url .rstrip ('/')}/ajax/chapters",
                    headers ={"X-Requested-With":"XMLHttpRequest"},
                    )
                    # Ahora el AJAX se intenta aunque no haya holder, asi que puede responder 404
                    # en sitios que antes ni se consultaban. Eso no es un error de la fuente: se
                    # deja la lista vacia en lugar de convertirlo en excepcion.
            if getattr (response ,"status_code",200 )<400 :
                items =self ._chapter_nodes (_parse_html (response .text ))
                if not items :
                    items =self ._fallback_chapter_nodes (_parse_html (response .text ))

        result :list [SourceChapter ]=[]
        seen_chapters :set [str ]=set ()
        for item in items :
            anchor =_first (item ,lambda node :node .tag =="a"and bool (node .attrs .get ("href")))
            if anchor is None :
                continue 
            title =anchor .text ().strip ()
            relative_image =_first (item ,lambda node :node .tag =="img"and not node .has_class ("thumb"))
            relative_link =_first (
            item ,
            lambda node :node .tag =="a"and node .parent is not None 
            and node .parent .tag =="span"and bool (node .attrs .get ("title")),
            )
            date =_first (item ,lambda node :node .tag =="span"and node .has_class ("chapter-release-date"))
            date_text =(
            relative_image .attrs .get ("alt","")if relative_image is not None 
            else relative_link .attrs .get ("title","")if relative_link is not None 
            else date .text ()if date else ""
            )
            chapter_url =urljoin (series_url ,anchor .attrs ["href"]).split ("?style=paged",1 )[0 ]
            if self .chapter_url_suffix and not chapter_url .endswith (self .chapter_url_suffix ):
                chapter_url +=self .chapter_url_suffix 
                # El fallback recorre li, div y tr por separado: en un markup anidado
                # el mismo ancla cae dentro de varios contenedores y entra una vez por cada uno.
            if chapter_url in seen_chapters :
                continue 
            seen_chapters .add (chapter_url )
            match =re .search (r"(?:chapter|cap(?:í|i)tulo|ch)[^\d]*(\d+(?:\.\d+)?)",title ,re .I )
            result .append (
            SourceChapter (
            source_id =chapter_url ,
            title =title or "Capítulo",
            series_id =series_id ,
            source_name =self .name ,
            number =float (match .group (1 ))if match else None ,
            language =self .language ,
            uploaded_at =self ._madara_date (date_text ),
            )
            )
        return result 

    def _madara_date (self ,value :str )->str |None :
        from calendar import monthrange 
        from datetime import datetime ,timedelta 

        text =value .strip ().casefold ()
        now =datetime .now ().replace (microsecond =0 )
        if text .startswith (("today","hoy")):
            return now .replace (hour =0 ,minute =0 ,second =0 ).isoformat ()
        if text .startswith (("yesterday","ayer")):
            return (now -timedelta (days =1 )).replace (hour =0 ,minute =0 ,second =0 ).isoformat ()
        relative =re .search (r"(\d+)",text )
        if relative and (text .startswith ("hace")or text .endswith (("ago","atrás"))):
            amount =int (relative .group ())
            if any (unit in text for unit in ("día","dia","day")):
                return (now -timedelta (days =amount )).isoformat ()
            if any (unit in text for unit in ("hora","hour")):
                return (now -timedelta (hours =amount )).isoformat ()
            if any (unit in text for unit in ("minuto","minute"," min")):
                return (now -timedelta (minutes =amount )).isoformat ()
            if any (unit in text for unit in ("segundo","second")):
                return (now -timedelta (seconds =amount )).isoformat ()
            if any (unit in text for unit in ("semana","week")):
                return (now -timedelta (days =amount *7 )).isoformat ()
            if any (unit in text for unit in ("mes","month")):
                total =now .year *12 +now .month -1 -amount 
                year ,month =divmod (total ,12 )
                return now .replace (
                year =year ,month =month +1 ,
                day =min (now .day ,monthrange (year ,month +1 )[1 ]),
                ).isoformat ()
            if any (unit in text for unit in ("año","year")):
                year =now .year -amount 
                return now .replace (year =year ,day =min (now .day ,monthrange (year ,now .month )[1 ])).isoformat ()
        numeric_format ={
        "MM/dd/yyyy":"%m/%d/%Y","dd/MM/yyyy":"%d/%m/%Y","yyyy-MM-dd":"%Y-%m-%d",
        }.get (self .date_format )
        if numeric_format :
            try :
                return datetime .strptime (value .strip (),numeric_format ).isoformat ()
            except ValueError :
                return None 
        if self .date_format not in {"d MMMM, yyyy","dd MMM yyyy","dd MMM, yyyy","dd MMMM, yyyy","MMM dd, yyyy","MMMM dd, yyyy"}:
            return None 
        months ={
        "january":1 ,"february":2 ,"march":3 ,"april":4 ,"may":5 ,"june":6 ,
        "july":7 ,"august":8 ,"september":9 ,"october":10 ,"november":11 ,"december":12 ,
        "enero":1 ,"febrero":2 ,"marzo":3 ,"abril":4 ,"mayo":5 ,"junio":6 ,
        "julio":7 ,"agosto":8 ,"septiembre":9 ,"octubre":10 ,"noviembre":11 ,"diciembre":12 ,
        "jan":1 ,"feb":2 ,"mar":3 ,"apr":4 ,"jun":6 ,"jul":7 ,"aug":8 ,
        "sep":9 ,"sept":9 ,"oct":10 ,"nov":11 ,"dec":12 ,
        "ene":1 ,"abr":4 ,"ago":8 ,"dic":12 ,
        }
        day_first =self .date_format .startswith (("d ","dd "))
        absolute =(
        re .fullmatch (r"(\d{1,2})\s+([^\s,]+),?\s+(\d{4})",text )
        if day_first 
        else re .fullmatch (r"([^\s]+)\s+(\d{1,2}),\s*(\d{4})",text )
        )
        month =absolute .group (2 ).rstrip (".")if absolute and day_first else absolute .group (1 ).rstrip (".")if absolute else ""
        if absolute and month in months :
            day =absolute .group (1 )if day_first else absolute .group (2 )
            return datetime (int (absolute .group (3 )),months [month ],int (day )).isoformat ()
        return None 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else chapter 
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",chapter_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        blocked =_first (
        root ,
        lambda node :node .has_class ("login-required")
        or (
        node .tag in {"form","input"}
        and (
        self .pages_profile =="captcha_guard"
        or node .attrs .get ("value","").lower ()in {"doğrula","verify"}
        )
        ),
        )
        if blocked is not None and self .pages_profile in {"login_guard","captcha_guard"}:
            raise ValueError ("El capítulo requiere iniciar sesión o resolver el captcha en WebView")

            # Perfil `redirect_form`: la pagina del capitulo NO trae las imagenes, trae un
            # formulario POST cuyo destino si las sirve. Portado de `pageListParse` de
            # TempleScanEsp.kt: sin esto el HTML inicial solo tiene el logo del sitio y el
            # parseo devuelve 0 paginas aunque la fuente funcione perfectamente.
            #
            # Si el formulario no esta se sigue con el flujo normal -igual que hace el Kotlin
            # con su `?: return super.pageListParse(document)`-: el sitio puede servir el
            # capitulo directamente segun el capitulo o si deja de usar la pasarela.
        if self .pages_profile =="redirect_form":
            formulario =_first (
            root ,
            lambda node :node .tag =="form"
            and node .attrs .get ("id")=="redirect-form"
            and node .attrs .get ("method","").lower ()=="post",
            )
            if formulario is not None and formulario .attrs .get ("action"):
                campos ={
                campo .attrs ["name"]:campo .attrs .get ("value","")
                for campo in formulario .descendants ("input")
                if campo .attrs .get ("name")
                }
                response =await self ._request (
                "POST",
                formulario .attrs ["action"],
                data =campos ,
                headers ={"Referer":str (response .url )},
                )
                response .raise_for_status ()
                root =_parse_html (response .text )

        profile_urls =self ._profile_page_urls (response .text ,str (response .url ))
        if self .pages_profile =="campaign":
            redirect =_first (
            root ,
            lambda node :node .tag =="a"
            and bool (parse_qs (urlparse (node .attrs .get ("href","")).query ).get ("a")),
            )
            if redirect is not None :
                target =unquote (parse_qs (urlparse (redirect .attrs ["href"]).query )["a"][0 ])
                campaign =await self ._request (
                "GET",
                f"{self .base_url }/campanha.php",
                params ={"auth":target },
                )
                campaign .raise_for_status ()
                campaign_root =_parse_html (campaign .text )
                profile_urls =[
                _image_url (image ,str (campaign .url ))
                for image in campaign_root .descendants ("img")
                if self ._has_class_ancestor (image ,"manga-content")
                ]
        containers =[
        node 
        for node in root .descendants ()
        if (node .tag =="div"and node .has_class ("page-break"))
        or (node .tag =="li"and node .has_class ("blocks-gallery-item"))
        ]
        images =[
        image 
        for container in containers 
        if (image :=_first (container ,lambda node :node .tag =="img"))is not None 
        ]
        reading =_first (root ,lambda node :node .has_class ("reading-content"))
        if reading is not None and self .pages_profile !="page_break_only":
            images .extend (reading .descendants ("img"))
        if not images :
            images =[
            image 
            for image in root .descendants ("img")
            if self ._has_reader_ancestor (image )
            ]

        urls =list (
        dict .fromkeys (
        url for image in images if (url :=_image_url (image ,str (response .url )))
        )
        )
        if profile_urls :
            urls =profile_urls 
        if not urls :
            script_text =response .text 
            encoded =re .search (
            r"""<script[^>]+src=["']data:text/javascript;base64,([^"']+)""",
            script_text ,
            re .I ,
            )
            if encoded :
                try :
                    script_text +=base64 .b64decode (encoded .group (1 )).decode ()
                except (ValueError ,UnicodeDecodeError ):
                    pass 
            match =re .search (r"""["']?images["']?\s*:\s*(\[.*?])""",script_text ,re .S )
            if match :
                try :
                    values =json .loads (match .group (1 ))
                except (json .JSONDecodeError ,TypeError ):
                    try :
                        values =ast .literal_eval (match .group (1 ))
                    except (ValueError ,SyntaxError ):
                        values =[]
                urls =[urljoin (str (response .url ),str (value ))for value in values ]
        if not urls :
            urls =_protected_page_urls (response .text ,str (response .url ))
        if self .pages_profile =="https":
            urls =[url .replace ("http://","https://",1 )for url in urls ]
        elif self .pages_profile =="skip_placeholder"and urls :
            if urls [0 ].split ("?",1 )[0 ].endswith ("/1-000001.jpg"):
                urls =urls [1 :]
                # Igual que en las portadas: el capitulo puede venir con las paginas en http
                # aunque el sitio hable https. Android descarta ese trafico y el lector se queda
                # en blanco. `pages_profile = "https"` ya lo parcheaba fuente a fuente; esto lo
                # cubre para todas, pero solo dentro del propio host.
        urls =[_mismo_host_seguro (url ,self .base_url )for url in urls ]
        return [
        SourcePage (
        source_id =url ,
        chapter_id =chapter_id ,
        index =index ,
        filename =url .rsplit ("/",1 )[-1 ].split ("?",1 )[0 ]or f"{index }.jpg",
        source_name =self .name ,
        )
        for index ,url in enumerate (urls ,1 )
        ]

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        url =page .source_id if isinstance (page ,SourcePage )else page 
        if not url :
            raise SourceNotFoundError ("Página Madara sin URL")
        parsed =urlparse (url )
        headers =dict (self .image_headers )
        if isinstance (page ,SourcePage )and not (
        self .strip_external_image_referer 
        and parsed .hostname !=urlparse (self .base_url ).hostname 
        ):
            headers .setdefault ("Referer",page .chapter_id )
        response =await self ._request (
        "GET",
        urlunparse (parsed ._replace (fragment ="")),
        headers =headers ,
        )
        response .raise_for_status ()
        content =response .content 
        if parsed .fragment and self .pages_profile =="scrambled":
            data =json .loads (unquote (parsed .fragment ))
            source =Image .open (io .BytesIO (content )).convert ("RGBA")
            output =Image .new ("RGBA",source .size )
            width ,height =int (data ["blockWidth"]),int (data ["blockHeight"])
            for dest_x ,dest_y ,src_x ,src_y ,*_ in data ["matrix"]:
                block =source .crop ((int (src_x ),int (src_y ),int (src_x )+width ,int (src_y )+height ))
                output .paste (block ,(int (dest_x ),int (dest_y )))
            buffer =io .BytesIO ()
            output .convert ("RGB").save (buffer ,"JPEG",quality =90 )
            content =buffer .getvalue ()
        return SourcePageContent (
        media_type ="image/jpeg"if parsed .fragment else response .headers .get ("Content-Type","image/jpeg"),
        chunks =iter ([content ]),
        )

    def _profile_page_urls (self ,html :str ,base_url :str )->list [str ]:
        if self .pages_profile =="arraydata":
            match =re .search (r"""<p[^>]+id=["']arraydata["'][^>]*>(.*?)</p>""",html ,re .I |re .S )
            return [urljoin (base_url ,value .strip ())for value in match .group (1 ).split (",")if value .strip ()]if match else []
        if self .pages_profile =="hentairead":
            base =re .search (r"""["']baseUrl["']\s*:\s*["']([^"']+)""",html )
            encoded =re .search (r"""\b(eyJ[A-Za-z0-9+/=_-]+)\b""",html )
            if not encoded :
                return []
            try :
                payload =json .loads (base64 .b64decode (encoded .group (1 )+"=="))
            except (ValueError ,json .JSONDecodeError ):
                return []
            images =payload .get ("data",{}).get ("chapter",{}).get ("images",[])
            return [urljoin (f"{base .group (1 )}/"if base else base_url ,str (item .get ("src","")))for item in images if item .get ("src")]
        patterns ={
        "cerise":r"""content\s*:\s*(\[[\s\S]*?])""",
        "preloaded":r"""chapter_preloaded_images\s*=\s*(\[[\s\S]*?])""",
        }
        if pattern :=patterns .get (self .pages_profile ):
            match =re .search (pattern ,html )
            if not match :
                return []
            try :
                values =json .loads (match .group (1 ))
            except json .JSONDecodeError :
                return []
            return [urljoin (base_url ,str (value ))for value in values ]
        if self .pages_profile =="base64_pages":
            match =re .search (r"""var\s+pages\s*=\s*\[([\s\S]*?)]""",html )
            if not match :
                return []
            result =[]
            for encoded in re .findall (r"""["']([^"']+)["']""",match .group (1 )):
                try :
                    result .append (base64 .b64decode (encoded ).decode ())
                except (ValueError ,UnicodeDecodeError ):
                    continue 
            return result 
        if self .pages_profile =="scrambled":
            result :list [str ]=[]
            for script in re .findall (r"<script[^>]*>([\s\S]*?)</script>",html ,re .I ):
                if "p,a,c,k,e,d"not in script :
                    continue 
                unpacked =self ._unpack_packer (script )
                width =re .search (r"""width:\s*["']?\s*\+?\s*(\d+)""",unpacked )
                height =re .search (r"""height:\s*["']?\s*\+?\s*(\d+)""",unpacked )
                matrix =re .search (r"(\[\s*\[.*?]])\s*;",unpacked ,re .S )
                image_url =re .search (r"""url\((['"]?)(.*?)\1\);""",unpacked )
                if all ((width ,height ,matrix ,image_url )):
                    data ={
                    "blockWidth":int (width .group (1 )),
                    "blockHeight":int (height .group (1 )),
                    "matrix":json .loads (matrix .group (1 )),
                    }
                    result .append (f"{urljoin (base_url ,image_url .group (2 ))}#{json .dumps (data ,separators =(',',':'))}")
            return result 
        return []

    @staticmethod 
    def _unpack_packer (source :str )->str :
        match =re .search (
        r"""\}\s*\(\s*(['"])(.*?)\1\s*,\s*(\d+)\s*,\s*\d+\s*,\s*(['"])(.*?)\4\.split\(\s*['"]\|['"]\s*\)""",
        source ,
        re .S ,
        )
        if match is None :
            return ""
        payload =bytes (match .group (2 ),"utf-8").decode ("unicode_escape")
        radix =int (match .group (3 ))
        words =match .group (5 ).split ("|")
        alphabet ="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def decode (value :str )->int :
            result =0 
            for char in value :
                result =result *radix +alphabet .index (char )
            return result 

        return re .sub (
        r"\b[0-9a-zA-Z]+\b",
        lambda found :words [index ]if (index :=decode (found .group ()))<len (words )and words [index ]else found .group (),
        payload ,
        )

    def _series (self ,html :str ,classes :tuple [str ,...])->list [SourceSeries ]:
        return self ._series_from_root (_parse_html (html ),classes )

    def _series_from_root (self ,root :_Node ,classes :tuple [str ,...])->list [SourceSeries ]:
        result :list [SourceSeries ]=[]
        seen :set [str ]=set ()
        for item in root .descendants ():
            if not any (item .has_class (name )for name in classes ):
                continue 
            title_box =_first (item ,lambda node :node .has_class ("post-title"))
            anchor =_first (title_box or item ,lambda node :node .tag =="a"and bool (node .attrs .get ("href")))
            if anchor is None :
                continue 
            source_id =urljoin (f"{self .base_url }/",anchor .attrs ["href"])
            title =anchor .text ().strip ()or anchor .attrs .get ("title","").strip ()
            if source_id in seen or not title :
                continue 
            seen .add (source_id )
            result .append (
            SourceSeries (
            source_id =source_id ,
            title =title ,
            source_name =self .name ,
            cover_url =_cover_url (item ,self .base_url ),
            web_url =source_id ,
            )
            )
        if result :
            return result 
        route =self .manga_substring .strip ("/")
        for anchor in root .descendants ("a"):
            href =anchor .attrs .get ("href","")
            parts =[part for part in urljoin (f"{self .base_url }/",href ).split ("?",1 )[0 ].split ("/")if part ]
            if not href or route not in parts :
                continue 
            route_index =parts .index (route )
            if len (parts )>route_index +2 :
                continue 
                # Tiene que quedar ALGO despues de la ruta. `/manga/` a secas es el indice del
                # custom post type, no una serie, y colaba en el listado como una entrada
                # fantasma sin portada titulada "MANGA" (manhuarm, tanto en browse como en
                # search). La guarda de arriba solo limitaba el maximo de segmentos.
            if len (parts )<=route_index +1 :
                continue 
            source_id =urljoin (f"{self .base_url }/",href )
            title =anchor .attrs .get ("title","").strip ()or anchor .text ().strip ()
            if title and source_id not in seen :
                seen .add (source_id )
                result .append (
                SourceSeries (
                source_id =source_id ,
                title =title ,
                source_name =self .name ,
                cover_url =_cover_url (anchor ,self .base_url ),
                web_url =source_id ,
                )
                )
        return result 

    @staticmethod 
    def _chapter_nodes (root :_Node )->list [_Node ]:
        return [
        node 
        for node in root .descendants ("li")
        if node .has_class ("wp-manga-chapter")
        ]

    @staticmethod 
    def _fallback_chapter_nodes (root :_Node )->list [_Node ]:
        result :list [_Node ]=[]
        for node in root .descendants ():
            if node .tag not in {"li","div","tr"}:
                continue 
            anchor =_first (node ,lambda item :item .tag =="a"and bool (item .attrs .get ("href")))
            if anchor is None :
                continue 
            value =f"{node .attrs .get ('class','')} {anchor .attrs ['href']} {anchor .text ()}".lower ()
            if any (marker in value for marker in ("chapter","chap","capitulo","capítulo","episode")):
                result .append (node )
        return result 

    @staticmethod 
    def _has_reader_ancestor (node :_Node )->bool :
        parent =node .parent 
        while parent is not None :
            marker =f"{parent .attrs .get ('id','')} {parent .attrs .get ('class','')}".lower ()
            if "related-reading"in marker :
                return False 
            if any (value in marker for value in ("reading-content","read-content","reader","ch-images")):
                return True 
            parent =parent .parent 
        return False 

    @staticmethod 
    def _has_class_ancestor (node :_Node ,class_name :str )->bool :
        parent =node .parent 
        while parent is not None :
            if parent .has_class (class_name ):
                return True 
            parent =parent .parent 
        return False 

    @staticmethod 
    def _has_id_ancestor (node :_Node ,identifier :str )->bool :
        parent =node .parent 
        while parent is not None :
            if parent .attrs .get ("id")==identifier :
                return True 
            parent =parent .parent 
        return False 

    async def _request (self ,method :str ,url :str ,**kwargs :Any )->Any :
        if self .fetcher is None :
            raise SourceNotFoundError (f"{self .display_name } no tiene fetcher inyectado")
        kwargs =_cuerpo_de_formulario (kwargs )
        return await self .fetcher .request (method ,url ,**kwargs )


try :
    from .madara import MadaraDetailsSource ,SourceChapter ,SourceFilter ,
    SourcePreference ,
    SourceSeries ,_first ,_image_url ,_parse_html 
except ImportError :
    pass 


class GenericSource (MadaraDetailsSource ):
    search_paths :tuple [str ,...]=("search","")
    popular_paths :tuple [str ,...]=("series","manga","comics","popular","")
    latest_paths :tuple [str ,...]=("latest","updates","series","manga","")

    async def search (self ,query :str ,limit :int =20 )->list [SourceSeries ]:
        for path in self .search_paths :
            for key in ("q","query","s","keyword"):
                try :
                    response =await self ._request (
                    "GET",
                    urljoin (f"{self .base_url }/",path ),
                    params ={key :query .strip (),"page":"1"},
                    )
                    if getattr (response ,"status_code",200 )>=400 :
                        continue 
                    values =self ._adaptive_series (response )
                    if values :
                        return values [:limit ]
                except Exception :
                    continue 
        return []

    async def browse (self ,kind :str ,page :int =1 )->list [SourceSeries ]:
        if kind not in {"popular","latest"}:
            return []
        paths =self .popular_paths if kind =="popular"else self .latest_paths 
        for path in paths :
            try :
                response =await self ._request (
                "GET",
                urljoin (f"{self .base_url }/",path ),
                params ={"page":str (page )},
                )
                if getattr (response ,"status_code",200 )>=400 :
                    continue 
                values =self ._adaptive_series (response )
                if values :
                    return values 
            except Exception :
                continue 
        return []

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else series 
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        result :list [SourceChapter ]=[]
        for anchor in root .descendants ("a"):
            href =anchor .attrs .get ("href","")
            title =anchor .text ().strip ()or anchor .attrs .get ("title","").strip ()
            marker =f"{href } {title }".lower ()
            if not href or not any (value in marker for value in ("chapter","chap","capitulo","capítulo","episode","bolum","read/")):
                continue 
            found =re .search (r"\d+(?:\.\d+)?",title )
            result .append (
            SourceChapter (
            source_id =urljoin (str (response .url ),href ),
            title =title or "Capítulo",
            series_id =series_id ,
            source_name =self .name ,
            number =float (found .group ())if found else None ,
            )
            )
        if not result :
            try :
                payload =response .json ()
            except (ValueError ,AttributeError ):
                payload =None 
            for item in self ._walk_dicts (payload ):
                title =str (item .get ("title")or item .get ("name")or "")
                item_id =item .get ("url")or item .get ("slug")or item .get ("id")
                if not title or item_id is None or "chap"not in json .dumps (item ).lower ():
                    continue 
                found =re .search (r"\d+(?:\.\d+)?",title )
                result .append (
                SourceChapter (
                source_id =urljoin (str (response .url ),str (item_id )),
                title =title ,
                series_id =series_id ,
                source_name =self .name ,
                number =float (found .group ())if found else None ,
                )
                )
        return list ({item .source_id :item for item in result }.values ())

    def _adaptive_series (self ,response )->list [SourceSeries ]:
        root =_parse_html (response .text )
        result :list [SourceSeries ]=[]
        seen :set [str ]=set ()
        for anchor in root .descendants ("a"):
            href =anchor .attrs .get ("href","")
            title =anchor .attrs .get ("title","").strip ()or anchor .text ().strip ()
            parent =anchor .parent 
            marker =""
            while parent is not None :
                marker +=f" {parent .attrs .get ('id','')} {parent .attrs .get ('class','')}"
                parent =parent .parent 
            if not href or not title or not any (value in marker .lower ()for value in ("manga","comic","series","novel","item","book")):
                continue 
            source_id =urljoin (str (response .url ),href )
            if source_id not in seen :
                seen .add (source_id )
                image =_first (anchor ,lambda node :node .tag =="img")
                if image is None and anchor .parent is not None :
                    image =_first (anchor .parent ,lambda node :node .tag =="img")
                result .append (
                SourceSeries (
                source_id =source_id ,
                title =title ,
                source_name =self .name ,
                cover_url =(
                _image_url (image ,str (response .url ))if image else None 
                ),
                web_url =source_id ,
                )
                )
        if result :
            return result 
        try :
            payload =response .json ()
        except (ValueError ,AttributeError ):
            return []
        for item in self ._walk_dicts (payload ):
            title =item .get ("title")or item .get ("name")
            item_id =item .get ("url")or item .get ("href")or item .get ("slug")or item .get ("id")
            if title and item_id is not None :
                source_id =urljoin (str (response .url ),str (item_id ))
                if source_id not in seen :
                    seen .add (source_id )
                    cover =(
                    item .get ("cover_url")
                    or item .get ("cover")
                    or item .get ("thumbnail")
                    or item .get ("image")
                    )
                    result .append (
                    SourceSeries (
                    source_id =source_id ,
                    title =str (title ),
                    source_name =self .name ,
                    cover_url =(
                    urljoin (str (response .url ),cover )
                    if isinstance (cover ,str )
                    else None 
                    ),
                    web_url =source_id ,
                    )
                    )
        return result 

    @staticmethod 
    def _walk_dicts (value ):
        if isinstance (value ,dict ):
            yield value 
            for child in value .values ():
                yield from GenericSource ._walk_dicts (child )
        elif isinstance (value ,list ):
            for child in value :
                yield from GenericSource ._walk_dicts (child )

class GeneratedGenericSource (GenericSource ):

    def get_preferences (self )->list [SourcePreference ]:
    # Autogenerated via heuristic port
        data =[]
        return [SourcePreference (**item )for item in data ]

    def get_filters (self )->list [SourceFilter ]:
    # Autogenerated via heuristic port
        data =[
        {
        "type":"select",
        "id":"generic_filter",
        "name":"Filtro",
        "options":[
        {
        "name":"3D Porn Comics",
        "value":"3d-porn-comics-xxx"
        },
        {
        "name":"Adventure Time Porn",
        "value":"adventure-time"
        },
        {
        "name":"American Dad Porn",
        "value":"american-dad-xx-porn-comix"
        },
        {
        "name":"Attack on Titan Hentai",
        "value":"attack-on-titan-hentai"
        },
        {
        "name":"Ben 10 Porn Comics",
        "value":"ben-10-porn-comics-v1"
        },
        {
        "name":"Chainsaw Man Porn Comics",
        "value":"porn-chainsaw-man-porn-comics"
        },
        {
        "name":"Dragon Ball Porn",
        "value":"dragon-ball-z-porn-comics-v1"
        },
        {
        "name":"Exclusive",
        "value":"exclusive"
        },
        {
        "name":"Furry Porn Comics",
        "value":"porn-comic-furry"
        },
        {
        "name":"Hentai Manga",
        "value":"xxxx-manga"
        },
        {
        "name":"Interracial Porn",
        "value":"interracial-porn-comix2-xxx"
        },
        {
        "name":"Kim Possible Porn",
        "value":"kim-possible-porn1"
        },
        {
        "name":"LoL Hentai",
        "value":"lol-hentai-xxx-comics1"
        },
        {
        "name":"My Hero Academia Hentai",
        "value":"hero-academia-porn-comic-v1"
        },
        {
        "name":"Naruto Hentai",
        "value":"naruto-hentai-comic4"
        },
        {
        "name":"One Piece Hentai",
        "value":"one-piece-hentai-v2"
        },
        {
        "name":"Palcomix",
        "value":"palcomix"
        },
        {
        "name":"Pokemon Porn",
        "value":"pokemon-porn-comics-v1"
        },
        {
        "name":"Porn Comics",
        "value":"porn-c0mics"
        },
        {
        "name":"Princess Peach Porn",
        "value":"princess-peach-porn-xxx"
        },
        {
        "name":"Rick and Morty Porn Comics",
        "value":"rick-and-morty-porn-comics"
        },
        {
        "name":"Simpsons Porn",
        "value":"simpsons-xxx-porn-comics"
        },
        {
        "name":"Sonic Porn Comics",
        "value":"sonic_porn-comics"
        },
        {
        "name":"Sword Art Online Hentai",
        "value":"sword-art-online-xxx-hentai1"
        },
        {
        "name":"Teen Titans Porn",
        "value":"teen-titans-porn-v1"
        },
        {
        "name":"Aarokira",
        "value":"aarokira"
        },
        {
        "name":"Accel Art",
        "value":"accel-art"
        },
        {
        "name":"Aerith Hentai",
        "value":"aerith-hentai"
        },
        {
        "name":"Afrobull",
        "value":"afrobull"
        },
        {
        "name":"agent aika",
        "value":"agent-aika"
        },
        {
        "name":"Ahri",
        "value":"ahri"
        },
        {
        "name":"Ahri Hentai",
        "value":"ahri-hentai"
        },
        {
        "name":"Ahsoka",
        "value":"ahsoka"
        },
        {
        "name":"Akali Hentai",
        "value":"akali-hentai1"
        },
        {
        "name":"Alien Girl",
        "value":"alien-girl"
        },
        {
        "name":"American Dragon",
        "value":"american-dragon"
        },
        {
        "name":"Amethyst",
        "value":"amethyst"
        },
        {
        "name":"Amity Blight",
        "value":"amity-blight"
        },
        {
        "name":"Among Us",
        "value":"among-us"
        },
        {
        "name":"Amphibia Porn",
        "value":"amphibia-porn"
        },
        {
        "name":"Amy",
        "value":"amy"
        },
        {
        "name":"Amy Rose Hentai",
        "value":"amy-rose-hentai"
        },
        {
        "name":"Amy Wong",
        "value":"amy-wong"
        },
        {
        "name":"Anal",
        "value":"anal"
        },
        {
        "name":"Anall",
        "value":"anall"
        },
        {
        "name":"Android 18 Hentai",
        "value":"android_18_xxx-hentai"
        },
        {
        "name":"Android 21 Hentai",
        "value":"android-21-hentai-xxx"
        },
        {
        "name":"Animal Crossing",
        "value":"animal-crossing-hentai-v1"
        },
        {
        "name":"Animated",
        "value":"animated"
        },
        {
        "name":"Ankha",
        "value":"ankha"
        },
        {
        "name":"Ann Possible",
        "value":"ann-possible"
        },
        {
        "name":"Arabatos",
        "value":"arabatos"
        },
        {
        "name":"Area",
        "value":"area"
        },
        {
        "name":"Aroma Sensei",
        "value":"aroma-sensei"
        },
        {
        "name":"Ashe Hentai",
        "value":"ashe-hentai"
        },
        {
        "name":"Asuka Hentai",
        "value":"asuka-henta1"
        },
        {
        "name":"Asuna Hentai",
        "value":"asuna-xxx-hentai"
        },
        {
        "name":"Atlantis The Lost Empire",
        "value":"atlantis-the-lost-empire"
        },
        {
        "name":"Atomic Heart Porn",
        "value":"atomic-heart-porn"
        },
        {
        "name":"Avatar Hentai",
        "value":"avatar-hentai-v1"
        },
        {
        "name":"Avengers",
        "value":"avengers"
        },
        {
        "name":"Batgirl",
        "value":"batgirl"
        },
        {
        "name":"Batman Porn Comics",
        "value":"batman-porn-comics-xxx"
        },
        {
        "name":"Bayonetta",
        "value":"bayonetta"
        },
        {
        "name":"BDSM",
        "value":"bdsm"
        },
        {
        "name":"Beast Boy",
        "value":"beast-boy"
        },
        {
        "name":"Big As",
        "value":"big-as"
        },
        {
        "name":"Big Ass",
        "value":"big-ass"
        },
        {
        "name":"Big Black Cock",
        "value":"big-black-cock"
        },
        {
        "name":"Big Boobs",
        "value":"big-boobs-xxx"
        },
        {
        "name":"Big Breasts",
        "value":"big-breasts-hentai"
        },
        {
        "name":"Big Cock",
        "value":"big-cock"
        },
        {
        "name":"Big Dick",
        "value":"big-dick"
        },
        {
        "name":"Big Hero 6",
        "value":"big-hero-6-porn"
        },
        {
        "name":"Big Tits",
        "value":"big-tit"
        },
        {
        "name":"Bigdad",
        "value":"bigdad"
        },
        {
        "name":"Bioshock Porn",
        "value":"bioshock-porn"
        },
        {
        "name":"Black Clover Porn",
        "value":"black-clover-porn-xxx"
        },
        {
        "name":"blackfire",
        "value":"blackfire"
        },
        {
        "name":"Blacknwhite",
        "value":"blacknwhite"
        },
        {
        "name":"Blaze",
        "value":"blaze"
        },
        {
        "name":"Bleach Hentai",
        "value":"bleach-porn-comics-xxx"
        },
        {
        "name":"Bloodborne",
        "value":"bloodborne"
        },
        {
        "name":"Blowjob",
        "value":"blowjob"
        },
        {
        "name":"Blue Archive",
        "value":"blue-archive"
        },
        {
        "name":"Boa Hancock Hentai",
        "value":"boa-hancock-hentai-xxx1"
        },
        {
        "name":"Boruto",
        "value":"boruto"
        },
        {
        "name":"Botbot",
        "value":"botbot"
        },
        {
        "name":"Bowsette Hentai",
        "value":"bowsette-hentai1"
        },
        {
        "name":"Brain Dead 13 Porn",
        "value":"brain-dead-13-porn"
        },
        {
        "name":"Brandy and mr Whiskers",
        "value":"brandy-and-mr-whiskers"
        },
        {
        "name":"Briar Hentai",
        "value":"briar-hentai"
        },
        {
        "name":"Bulma Hentai",
        "value":"bulma-h3ntaix"
        },
        {
        "name":"Bunny Girl",
        "value":"bunny-girl"
        },
        {
        "name":"Carmelita",
        "value":"carmelita"
        },
        {
        "name":"carrot",
        "value":"carrot"
        },
        {
        "name":"Catgirl",
        "value":"catgirl"
        },
        {
        "name":"Catunder",
        "value":"catunder"
        },
        {
        "name":"Caulifla Hentai",
        "value":"caulifla-hentai"
        },
        {
        "name":"chainsaw man porn comic",
        "value":"chainsaw-man-porn-comic"
        },
        {
        "name":"Chara",
        "value":"chara"
        },
        {
        "name":"Cheelai Hentai",
        "value":"cheelai-hentai"
        },
        {
        "name":"Cherry Road Porn Comics",
        "value":"cherry-road-porn-comics"
        },
        {
        "name":"Chi-Chi Hentai",
        "value":"chi-chi_hentai"
        },
        {
        "name":"Chloe",
        "value":"chloe"
        },
        {
        "name":"ChoChoX",
        "value":"chochox"
        },
        {
        "name":"Chun-Li",
        "value":"chun-li"
        },
        {
        "name":"Coco Bandicoot Hentai",
        "value":"coco-bandicoot-hentai"
        },
        {
        "name":"Code Lyoko",
        "value":"code-lyoko"
        },
        {
        "name":"Connie Maheswaran",
        "value":"connie-maheswaran"
        },
        {
        "name":"Cookie Run Porn",
        "value":"cookie-run-porn"
        },
        {
        "name":"Cortana Porn",
        "value":"cortana-porn"
        },
        {
        "name":"Crash Bandicoot",
        "value":"crash-bandicoot-porn"
        },
        {
        "name":"CrazyDad3D",
        "value":"crazydad3d"
        },
        {
        "name":"Cream the Rabbit",
        "value":"cream-the-rabbit"
        },
        {
        "name":"CrockComix",
        "value":"crockcomix"
        },
        {
        "name":"Crossdressing",
        "value":"crossdressing"
        },
        {
        "name":"Cumshot",
        "value":"cumshot"
        },
        {
        "name":"Cunnilingus",
        "value":"cunnilingus"
        },
        {
        "name":"Cuphead Hentai",
        "value":"cuphead-hentai"
        },
        {
        "name":"Cyberpunk Edgerunners Porn",
        "value":"cyberpunk-edgerunners-p0rn-comics"
        },
        {
        "name":"Daisy Hentai",
        "value":"daisy-hentai"
        },
        {
        "name":"Dandadan Hentai",
        "value":"dandadan-comic2-hentai"
        },
        {
        "name":"Danganronpa",
        "value":"danganronpa"
        },
        {
        "name":"Danny Phantom Porn",
        "value":"danny-phantom_porn-comics"
        },
        {
        "name":"Daphne Hentai",
        "value":"daphne-hentai"
        },
        {
        "name":"Dark Souls",
        "value":"dark-souls"
        },
        {
        "name":"Darkstalkers Porn",
        "value":"darkstalkers-porn"
        },
        {
        "name":"Dawn Hentai",
        "value":"dawn-hentai"
        },
        {
        "name":"DC Porn",
        "value":"dc-porn"
        },
        {
        "name":"Deadpool",
        "value":"deadpool"
        },
        {
        "name":"Dee Dee",
        "value":"dee-dee"
        },
        {
        "name":"Deepthroat",
        "value":"deepthroat"
        },
        {
        "name":"Deku",
        "value":"deku"
        },
        {
        "name":"Deltarune Porn",
        "value":"deltarune-porn"
        },
        {
        "name":"Demon Girl",
        "value":"demon-girl"
        },
        {
        "name":"Demon Slayer Nezuko Porn",
        "value":"demon-slayer-nezuko-porn1"
        },
        {
        "name":"Demon Slayer Porn",
        "value":"demon-slayer-porn-comic1-xxx"
        },
        {
        "name":"Devil May Cry",
        "value":"devil-may-cry"
        },
        {
        "name":"Devvil May Cry Porn",
        "value":"devvil-may-cry-porn"
        },
        {
        "name":"Dexter Mom Hentai",
        "value":"dexter-mom-hentai1"
        },
        {
        "name":"Dexter Porn Comics",
        "value":"dexter-porn-comics"
        },
        {
        "name":"Diane",
        "value":"diane"
        },
        {
        "name":"Digimon XXX",
        "value":"digimon-xxx"
        },
        {
        "name":"Dipper",
        "value":"dipper"
        },
        {
        "name":"Diva Hentai",
        "value":"diva-hentai"
        },
        {
        "name":"Doom",
        "value":"doom"
        },
        {
        "name":"Dora",
        "value":"dora"
        },
        {
        "name":"Double Anal",
        "value":"double-anal"
        },
        {
        "name":"Double Penetration",
        "value":"double-penetration"
        },
        {
        "name":"Dr. Stone",
        "value":"dr-stone-x"
        },
        {
        "name":"Dragon Quest Porn",
        "value":"dragon-quest-porn"
        },
        {
        "name":"Drah Navlag",
        "value":"drah-navlag"
        },
        {
        "name":"Dsan",
        "value":"dsan"
        },
        {
        "name":"dungeon and dragons",
        "value":"dungeon-and-dragons"
        },
        {
        "name":"Ed Edd n Eddy Porn",
        "value":"ed-edd-n-eddy-porn"
        },
        {
        "name":"Elden Ring",
        "value":"elden-ring"
        },
        {
        "name":"Elf",
        "value":"elf"
        },
        {
        "name":"Elizabeth Liones porn",
        "value":"elizabeth-liones-porn"
        },
        {
        "name":"Elsa",
        "value":"elsa-hentai"
        },
        {
        "name":"Emma Frost",
        "value":"emma-frost"
        },
        {
        "name":"Epic Seven Hentai",
        "value":"epic-seven-hentai"
        },
        {
        "name":"Ero Mantic",
        "value":"ero-mantic"
        },
        {
        "name":"Evangelion Hentai",
        "value":"henta1-evangeli0n"
        },
        {
        "name":"Evee",
        "value":"evee"
        },
        {
        "name":"Evelynn",
        "value":"evelynn"
        },
        {
        "name":"Fairy Tail",
        "value":"fairy-tail-hentai-v0"
        },
        {
        "name":"Fallout XXX",
        "value":"fallout-xxx-hentai"
        },
        {
        "name":"Family Guy Porn Comics",
        "value":"family-guy-porn_comix"
        },
        {
        "name":"Fate Grand Order Hentai",
        "value":"fate-grand-order-hentai-v0"
        },
        {
        "name":"Feith Noir",
        "value":"feith-noir"
        },
        {
        "name":"Felsala",
        "value":"felsala"
        },
        {
        "name":"Final Fantasy Hentai",
        "value":"final-fantasy-hentai-v2"
        },
        {
        "name":"Finn",
        "value":"finn"
        },
        {
        "name":"Fiona",
        "value":"fiona"
        },
        {
        "name":"Fiora",
        "value":"fiora"
        },
        {
        "name":"Fire Emblem",
        "value":"fire-emblem"
        },
        {
        "name":"five nights at freddy's Porn",
        "value":"five-nights-at-freddys-porn"
        },
        {
        "name":"Flame Princess",
        "value":"flame-princess"
        },
        {
        "name":"Footjob",
        "value":"footjob"
        },
        {
        "name":"Fortnite",
        "value":"fortnite"
        },
        {
        "name":"Foster’s Home for Imaginary Friends Porn",
        "value":"fosters-home-for-imaginary-friends-porn"
        },
        {
        "name":"Francine Porn Comics",
        "value":"francine-porn-comics"
        },
        {
        "name":"Frankie Foster Hentai",
        "value":"frankie-foster-hentai"
        },
        {
        "name":"Fred Perry",
        "value":"fred-perry"
        },
        {
        "name":"Frieren Porn",
        "value":"frieren-porn"
        },
        {
        "name":"Frozen",
        "value":"frozen"
        },
        {
        "name":"Fubuki Hentai",
        "value":"fubuki-hentai"
        },
        {
        "name":"Full Color",
        "value":"full-color-comic"
        },
        {
        "name":"FunsexyDB",
        "value":"funsexydb"
        },
        {
        "name":"Furret El Furro",
        "value":"furret-el-furro"
        },
        {
        "name":"Futa Comic",
        "value":"futanari-comic-xxx"
        },
        {
        "name":"Futurama Porn Comics",
        "value":"futurama_porn-c0mics1"
        },
        {
        "name":"Gansoman",
        "value":"gansoman"
        },
        {
        "name":"Gardevoir Hentai",
        "value":"gardevoir-hentaix"
        },
        {
        "name":"Garnet Hentai",
        "value":"garnet-hentai"
        },
        {
        "name":"gay porn comics",
        "value":"porn-comics-gay"
        },
        {
        "name":"Gemma Hentai",
        "value":"gemma-hentai"
        },
        {
        "name":"Gender Bender",
        "value":"gender-bender"
        },
        {
        "name":"Genshin Impact Porn",
        "value":"genshin-impact-p0rnx"
        },
        {
        "name":"Gine Hentai",
        "value":"gine-hentai"
        },
        {
        "name":"Girls und Panzer Hentai",
        "value":"girls-und-panzer_hentai"
        },
        {
        "name":"Girls' Frontline Hentai",
        "value":"girls-frontline-hentai"
        },
        {
        "name":"Glitch Techs",
        "value":"glitch-techs"
        },
        {
        "name":"Glory Hole",
        "value":"glory-hole"
        },
        {
        "name":"Goblin",
        "value":"goblin"
        },
        {
        "name":"Goku",
        "value":"goku"
        },
        {
        "name":"gold digger",
        "value":"gold-digger"
        },
        {
        "name":"Granblue Fantasy",
        "value":"granblue-fantasy"
        },
        {
        "name":"Gravity Falls Porn",
        "value":"gravity-falls-porn-comics"
        },
        {
        "name":"Great Fairy Hentai",
        "value":"great-fairy-hentai"
        },
        {
        "name":"Greendogg",
        "value":"greendogg"
        },
        {
        "name":"Guilty Gear",
        "value":"guilty-gear"
        },
        {
        "name":"Gumball",
        "value":"gumball"
        },
        {
        "name":"Gwen",
        "value":"gwen"
        },
        {
        "name":"Gwen Stacy",
        "value":"gwen-stacy"
        },
        {
        "name":"GygerBeen",
        "value":"gygerbeen"
        },
        {
        "name":"Hagfish",
        "value":"hagfish"
        },
        {
        "name":"Haikon Knight",
        "value":"haikon-knight"
        },
        {
        "name":"Halo",
        "value":"halo"
        },
        {
        "name":"Handjob",
        "value":"handjob"
        },
        {
        "name":"Harley Quinn",
        "value":"harley-quinn"
        },
        {
        "name":"Harry Potter",
        "value":"harry-potter"
        },
        {
        "name":"Hatsune Miku Hentai",
        "value":"hatsune-miku-hentai"
        },
        {
        "name":"Hazbin Hotel",
        "value":"hazbin-hotel-pornx"
        },
        {
        "name":"Hekapoo",
        "value":"hekapoo-hentai"
        },
        {
        "name":"Helluva Boss Porn",
        "value":"helluva-boss-comics-xxx"
        },
        {
        "name":"Hermione Granger",
        "value":"hermione-granger"
        },
        {
        "name":"Hermit Moth Hentai",
        "value":"hermit-moth-hentai"
        },
        {
        "name":"HermitMoth",
        "value":"hermitmoth"
        },
        {
        "name":"Hey Arnold",
        "value":"hey-arnold"
        },
        {
        "name":"Highschool DxD",
        "value":"highschool-dxd"
        },
        {
        "name":"Hilda",
        "value":"hilda"
        },
        {
        "name":"Hilda Porn",
        "value":"hilda-porn"
        },
        {
        "name":"Himiko Toga Hentai",
        "value":"himiko-toga-hentai"
        },
        {
        "name":"Hinata Hentai",
        "value":"hinata-hentai-xxx2"
        },
        {
        "name":"Honey Lemon Hentai",
        "value":"honey-lemon-hentai"
        },
        {
        "name":"Hornyx",
        "value":"hornyx"
        },
        {
        "name":"Hot Step Sister",
        "value":"hot-step-sister"
        },
        {
        "name":"Hotel Transylvania Porn Comics",
        "value":"hotel_transylvania-porn-comics"
        },
        {
        "name":"Huge Breasts",
        "value":"huge-breasts"
        },
        {
        "name":"Ice Queen Hentai",
        "value":"ice-queen-hentai"
        },
        {
        "name":"Impa Hentai",
        "value":"impa-hentai"
        },
        {
        "name":"Incestibles",
        "value":"incestibles"
        },
        {
        "name":"Incognitymous",
        "value":"incognitymous-comic"
        },
        {
        "name":"Ino",
        "value":"ino"
        },
        {
        "name":"Ino Hentai",
        "value":"ino-hentai-xxx"
        },
        {
        "name":"Inside Out",
        "value":"inside-out"
        },
        {
        "name":"Inspector Gadget",
        "value":"inspector-gadget"
        },
        {
        "name":"Inuyuru",
        "value":"inuyuru"
        },
        {
        "name":"Invader Zim Porn",
        "value":"invader-zim-porn"
        },
        {
        "name":"INVINCIBLE Porn",
        "value":"invincible-porn"
        },
        {
        "name":"Irelia Hentai",
        "value":"irelia-hentai"
        },
        {
        "name":"IS Infinite Stratos",
        "value":"is-infinite-stratos"
        },
        {
        "name":"Isabelle",
        "value":"isabelle"
        },
        {
        "name":"Itsuka Kendo Hentai",
        "value":"itsuka-kendo-hentai"
        },
        {
        "name":"Jack and Daxter",
        "value":"jack-and-daxter"
        },
        {
        "name":"Jackie Lynn",
        "value":"jackie-lynn"
        },
        {
        "name":"Jaguar",
        "value":"jaguar"
        },
        {
        "name":"Jaiden XXX",
        "value":"jaiden-xxx"
        },
        {
        "name":"Janna",
        "value":"janna"
        },
        {
        "name":"Jasmine",
        "value":"jasmine"
        },
        {
        "name":"JDseal",
        "value":"jdseal"
        },
        {
        "name":"Jenny Hentai",
        "value":"jenny-hentai"
        },
        {
        "name":"Jessica Rabbit",
        "value":"jessica-rabbit"
        },
        {
        "name":"Jessie Porn",
        "value":"jessie-porn"
        },
        {
        "name":"Jill Valentine",
        "value":"jill-valentine"
        },
        {
        "name":"Jimmy Neutron",
        "value":"jimmy-neutron"
        },
        {
        "name":"Jinx",
        "value":"jinx"
        },
        {
        "name":"Jinx Hentai",
        "value":"jinx-hentai-xxx-v2"
        },
        {
        "name":"Jirou Hentai",
        "value":"jirou_hentai"
        },
        {
        "name":"Jlullaby",
        "value":"jlullaby"
        },
        {
        "name":"Johnny Test",
        "value":"johnny-test"
        },
        {
        "name":"Jujutsu Kaisen",
        "value":"jujutsu-kaisen"
        },
        {
        "name":"Justice League",
        "value":"justice-league"
        },
        {
        "name":"JZerosk",
        "value":"jzerosk"
        },
        {
        "name":"Kairi",
        "value":"kairi"
        },
        {
        "name":"Kale Hentai",
        "value":"kale-hentai-xxx"
        },
        {
        "name":"Kantai Collection Hentai",
        "value":"kantai-collection-hentai"
        },
        {
        "name":"Katarina Hentai",
        "value":"katarina-hentai"
        },
        {
        "name":"Kefla Hentai",
        "value":"kefla-hentai"
        },
        {
        "name":"Kid Icarus",
        "value":"kid-icarus"
        },
        {
        "name":"Kill la Kill",
        "value":"kill-la-kill-hentai"
        },
        {
        "name":"Kim Possible",
        "value":"kim-possible-porn1"
        },
        {
        "name":"kingdom hearts",
        "value":"kingdom-hearts"
        },
        {
        "name":"Kinkymation",
        "value":"kinkymation-hentai"
        },
        {
        "name":"Kirlia Hentai",
        "value":"kirlia-hentai"
        },
        {
        "name":"Kiryuin Satsuki Hentai",
        "value":"kiryuin-satsuki-hentai"
        },
        {
        "name":"Kogeikun",
        "value":"kogeikun"
        },
        {
        "name":"Lady Dimitrescu Porn",
        "value":"lady_dimitrescu-porn"
        },
        {
        "name":"Lady Dmittrescu",
        "value":"lady-dmittrescu"
        },
        {
        "name":"Lapis Lazuli",
        "value":"lapis-lazuli"
        },
        {
        "name":"Lara Croft",
        "value":"lara-croft"
        },
        {
        "name":"Leela Porn",
        "value":"leela-porn"
        },
        {
        "name":"Leona",
        "value":"leona"
        },
        {
        "name":"Lesbians",
        "value":"lesbians"
        },
        {
        "name":"Lilo and Stitch",
        "value":"lilo-and-stitch"
        },
        {
        "name":"Lina Inverse",
        "value":"lina-inverse"
        },
        {
        "name":"Lisa Simpson Porn",
        "value":"lisa-simpson-porn"
        },
        {
        "name":"Lissandra",
        "value":"lissandra"
        },
        {
        "name":"Little Nightmares",
        "value":"little-nightmares"
        },
        {
        "name":"Little Nightmares Porn",
        "value":"little-nightmares-porn"
        },
        {
        "name":"Little Witch Academia",
        "value":"little-witch-academia"
        },
        {
        "name":"Lola Bunny",
        "value":"lola-bunny"
        },
        {
        "name":"Looney Tunes Porn",
        "value":"looney-tunes-porn"
        },
        {
        "name":"Lori Loud Porn",
        "value":"lori-loud-porn"
        },
        {
        "name":"Luna Loud",
        "value":"luna-loud"
        },
        {
        "name":"Lux",
        "value":"lux"
        },
        {
        "name":"Luz Noceda",
        "value":"luz-noceda"
        },
        {
        "name":"Mabel",
        "value":"mabel"
        },
        {
        "name":"Macergo",
        "value":"macergo"
        },
        {
        "name":"Madeline Fenton",
        "value":"madeline-fenton"
        },
        {
        "name":"Mai Hentai",
        "value":"mai-hentai"
        },
        {
        "name":"Malefica",
        "value":"malefica"
        },
        {
        "name":"Maleficient",
        "value":"maleficient"
        },
        {
        "name":"Mana World",
        "value":"mana-world"
        },
        {
        "name":"Manaworld",
        "value":"manaworld"
        },
        {
        "name":"Marceline",
        "value":"marceline"
        },
        {
        "name":"Marco Diaz",
        "value":"marco-diaz"
        },
        {
        "name":"Marge Simpson Porn",
        "value":"marge-simpson-porn-xxx"
        },
        {
        "name":"Mario Bros",
        "value":"mario-bros"
        },
        {
        "name":"Marvel",
        "value":"marvel"
        },
        {
        "name":"Marvel Rivals Porn",
        "value":"marvel-rivals-porn"
        },
        {
        "name":"Mass Effect Porn",
        "value":"mass-effect-porn"
        },
        {
        "name":"Masturbation",
        "value":"masturbation"
        },
        {
        "name":"May Hentai",
        "value":"may-hentai-xxx"
        },
        {
        "name":"Meego",
        "value":"meego"
        },
        {
        "name":"Meg Griffin",
        "value":"meg-griffin"
        },
        {
        "name":"Mega Man",
        "value":"mega-man"
        },
        {
        "name":"Mei Hatsume Hentai",
        "value":"mei-hatsume-hentai"
        },
        {
        "name":"Mei Hentai",
        "value":"mei-hentai"
        },
        {
        "name":"Melkor Mancin",
        "value":"melkor-mancin"
        },
        {
        "name":"MelkorMancin",
        "value":"melkormancin"
        },
        {
        "name":"Mercy Hentai",
        "value":"mercy-hentai"
        },
        {
        "name":"Metroid",
        "value":"metroid"
        },
        {
        "name":"Midna Hentai",
        "value":"midna-hentai"
        },
        {
        "name":"Mif",
        "value":"mif"
        },
        {
        "name":"Mifl",
        "value":"mifl"
        },
        {
        "name":"Mikasa Hentai",
        "value":"mikasa-henta1"
        },
        {
        "name":"Miko",
        "value":"miko"
        },
        {
        "name":"Miles",
        "value":"miles"
        },
        {
        "name":"Miles Morales Porn Comics",
        "value":"miles-morales-porn-comics"
        },
        {
        "name":"Milf",
        "value":"milf-xxx"
        },
        {
        "name":"Milfs",
        "value":"milfs"
        },
        {
        "name":"Milk",
        "value":"milk"
        },
        {
        "name":"Milky Bunny",
        "value":"milky-bunny"
        },
        {
        "name":"Millie",
        "value":"millie"
        },
        {
        "name":"Milo Murphy's Law Porn",
        "value":"milo-murphys-law-porn"
        },
        {
        "name":"Mina Ashido Hentai",
        "value":"mina-ashido-hentai1"
        },
        {
        "name":"Minako Aino",
        "value":"minako-aino"
        },
        {
        "name":"Minecraft Porn",
        "value":"minecraft-porn"
        },
        {
        "name":"Mineta",
        "value":"mineta"
        },
        {
        "name":"Miraculous Ladybug",
        "value":"miraculous-ladybug"
        },
        {
        "name":"Misato Katsuragi Hentai",
        "value":"misato-katsuragi-hentai"
        },
        {
        "name":"Miss Heed",
        "value":"miss-heed"
        },
        {
        "name":"Misty Hentai",
        "value":"misty-hentai"
        },
        {
        "name":"Mitsuki Hentai",
        "value":"mitsuki-hentai"
        },
        {
        "name":"Mob Psycho 100",
        "value":"mob-psycho-100"
        },
        {
        "name":"Mobile Suit Gundam",
        "value":"mobile-suit-gundam"
        },
        {
        "name":"Momo Hentai",
        "value":"momo_hentai"
        },
        {
        "name":"Mona",
        "value":"mona"
        },
        {
        "name":"Monster Girl",
        "value":"monster-girl"
        },
        {
        "name":"monster hunter",
        "value":"monster-hunter"
        },
        {
        "name":"Morgana Hentai",
        "value":"morgana-hentai"
        },
        {
        "name":"Mossy Froot",
        "value":"mossy-froot"
        },
        {
        "name":"Mr. Jean Gobax",
        "value":"mr-jean-gobax"
        },
        {
        "name":"Mr.E",
        "value":"mr-e"
        },
        {
        "name":"My Bad Bunny",
        "value":"my-bad-bunny"
        },
        {
        "name":"My LIfe as a Teenage Robot",
        "value":"my-life-as-a-teenage-robot"
        },
        {
        "name":"My Little Pony Porn Comics",
        "value":"porn-comic-xxx-my-little-pony1"
        },
        {
        "name":"Nami Hentai",
        "value":"nami-xxx-hentai-1"
        },
        {
        "name":"Nana Shimura Hentai",
        "value":"nana-shimura-hentai"
        },
        {
        "name":"Neeko",
        "value":"neeko"
        },
        {
        "name":"Nejire Hado",
        "value":"nejire-hado"
        },
        {
        "name":"Nemona Hentai",
        "value":"nemona-hentai"
        },
        {
        "name":"Nemuri Kayama Hentai",
        "value":"nemuri-kayama-hentai"
        },
        {
        "name":"NGTVisualStudio",
        "value":"ngtvisualstudio"
        },
        {
        "name":"Nico Robin Hentai",
        "value":"nico-robin-xxx-hentai2"
        },
        {
        "name":"Nicole Watterson",
        "value":"nicole-watterson"
        },
        {
        "name":"Nier Automata",
        "value":"nier-automata"
        },
        {
        "name":"Nisego",
        "value":"nisego"
        },
        {
        "name":"Oban Star Racers",
        "value":"oban-star-racers"
        },
        {
        "name":"Ochako Uraraka",
        "value":"ochako-uraraka"
        },
        {
        "name":"OK K.O.! Let's Be Heroes Porn",
        "value":"ok-k-o-lets-be-heroes-porn"
        },
        {
        "name":"One Punch Man",
        "value":"comic-hentai-one-punch-man-porn"
        },
        {
        "name":"OnGoing",
        "value":"ongoing"
        },
        {
        "name":"Ousama Ranking",
        "value":"ousama-ranking"
        },
        {
        "name":"Overwatch Porn",
        "value":"overwatch-porn"
        },
        {
        "name":"Pacifica Porn",
        "value":"pacifica-porn"
        },
        {
        "name":"Palutena Hentai",
        "value":"palutena-hentai"
        },
        {
        "name":"Panchy",
        "value":"panchy"
        },
        {
        "name":"Paya Hentai",
        "value":"paya-hentai"
        },
        {
        "name":"Peach Hentai",
        "value":"peach-hentai"
        },
        {
        "name":"Pearl",
        "value":"pearl"
        },
        {
        "name":"Pedverse",
        "value":"pedverse"
        },
        {
        "name":"Peg",
        "value":"peg"
        },
        {
        "name":"Persona 2",
        "value":"persona-2"
        },
        {
        "name":"Persona 3",
        "value":"persona-3"
        },
        {
        "name":"Persona 4",
        "value":"persona-4-xxx"
        },
        {
        "name":"persona 5",
        "value":"persona-5"
        },
        {
        "name":"Phineas and Ferb",
        "value":"phineas-and-ferb"
        },
        {
        "name":"Pink Pawg",
        "value":"pink-pawg"
        },
        {
        "name":"Pokemon Scarlet",
        "value":"pokemon-scarlet"
        },
        {
        "name":"PokuArts",
        "value":"pokuarts"
        },
        {
        "name":"Pony Tsunotori Hentai",
        "value":"pony-tsunotori-hentai"
        },
        {
        "name":"Poppy",
        "value":"poppy"
        },
        {
        "name":"Porn Parody",
        "value":"porn-parody"
        },
        {
        "name":"Princess Bubblegum",
        "value":"princess-bubblegum"
        },
        {
        "name":"Princess Daisy",
        "value":"princess-daisy"
        },
        {
        "name":"Princess Peach",
        "value":"princess-peach"
        },
        {
        "name":"Prison School Hentai",
        "value":"prison-school-hentai"
        },
        {
        "name":"Priyanka Hentai",
        "value":"priyanka-hentai"
        },
        {
        "name":"Promare",
        "value":"promare"
        },
        {
        "name":"Purah Hentai",
        "value":"purah-hentai"
        },
        {
        "name":"Rainbow Mika",
        "value":"rainbow-mika"
        },
        {
        "name":"Rainbow Six Siege Porn",
        "value":"rainbow-six-siege-porn"
        },
        {
        "name":"Ranma Porn",
        "value":"ranma-porn-comic"
        },
        {
        "name":"Ratchet and Clank Porn",
        "value":"ratchet-and-clank-porn"
        },
        {
        "name":"Raven",
        "value":"raven"
        },
        {
        "name":"Raven xxx",
        "value":"raven-hentaaii"
        },
        {
        "name":"Razter",
        "value":"razter"
        },
        {
        "name":"Rebecca Cyberpunk Porn",
        "value":"rebecca-cyberpunk-p0rn"
        },
        {
        "name":"Rei Hentai",
        "value":"rei-hentai"
        },
        {
        "name":"RelatedGuy",
        "value":"relatedguy-porn"
        },
        {
        "name":"Rem Hentai",
        "value":"rem-hentai"
        },
        {
        "name":"Renamon",
        "value":"renamon"
        },
        {
        "name":"Resident Evil",
        "value":"resident-evil-porn"
        },
        {
        "name":"Rezero Hentai",
        "value":"rezero-hentai"
        },
        {
        "name":"Riukykappa",
        "value":"riukykappa"
        },
        {
        "name":"Riven",
        "value":"riven"
        },
        {
        "name":"Rivet Hentai",
        "value":"rivet-hentai"
        },
        {
        "name":"road to el dorado",
        "value":"road-to-el-dorado"
        },
        {
        "name":"Rosalina Hentai",
        "value":"rosalina-hentai"
        },
        {
        "name":"Rouge",
        "value":"rouge"
        },
        {
        "name":"Roumgu",
        "value":"roumgu-xxx"
        },
        {
        "name":"Rudolph",
        "value":"rudolph"
        },
        {
        "name":"Rumi Usagiyama Hentai",
        "value":"rumi-usagiyama-hentai"
        },
        {
        "name":"Sailor Moon Hentai",
        "value":"sailor-moon-hentai"
        },
        {
        "name":"Sakura Hentai",
        "value":"sakura_h3ntai"
        },
        {
        "name":"Sally",
        "value":"sally"
        },
        {
        "name":"Samantha Manson",
        "value":"samantha-manson"
        },
        {
        "name":"Samsung Sam Hentai",
        "value":"samsung-sam-hentai"
        },
        {
        "name":"Samus Hentai",
        "value":"samus-hentai"
        },
        {
        "name":"Sandy",
        "value":"sandy"
        },
        {
        "name":"Sarada Hentai",
        "value":"sarada-hentai"
        },
        {
        "name":"Scooby Doo Hentai",
        "value":"scooby-doo-hentai"
        },
        {
        "name":"Seiko Hentai",
        "value":"seiko-hentai"
        },
        {
        "name":"Seraphine",
        "value":"seraphine"
        },
        {
        "name":"Sex Toys",
        "value":"sex-toys"
        },
        {
        "name":"Shadbase",
        "value":"shadbase-comics"
        },
        {
        "name":"Shantae",
        "value":"shantae-hentai"
        },
        {
        "name":"She-Hulk",
        "value":"she-hulk-porn"
        },
        {
        "name":"She-Ra and the Princesses of Power Porn",
        "value":"she-ra-and-the-princesses-of-power-porn"
        },
        {
        "name":"Shego",
        "value":"shego"
        },
        {
        "name":"shin-chan",
        "value":"shin-chan"
        },
        {
        "name":"Sidney",
        "value":"sidney"
        },
        {
        "name":"Sillygirl",
        "value":"sillygirl"
        },
        {
        "name":"Six",
        "value":"six"
        },
        {
        "name":"Skullgirls",
        "value":"skullgirls"
        },
        {
        "name":"Small Tits",
        "value":"small-tits"
        },
        {
        "name":"Smash Bros",
        "value":"smash-bros"
        },
        {
        "name":"Sonic",
        "value":"sonic"
        },
        {
        "name":"Soraka",
        "value":"soraka"
        },
        {
        "name":"Soraka Hentai",
        "value":"soraka-hentai"
        },
        {
        "name":"Soul Knight",
        "value":"soul-knight"
        },
        {
        "name":"South Park",
        "value":"south-park"
        },
        {
        "name":"Spectra",
        "value":"spectra"
        },
        {
        "name":"spidergirl",
        "value":"spidergirl-xxx"
        },
        {
        "name":"Spiderman Porn Comics",
        "value":"porn-comics-xxx-spiderman1"
        },
        {
        "name":"Spinel",
        "value":"spinel"
        },
        {
        "name":"Splatoon Porn Comics",
        "value":"splaton-porn-comic"
        },
        {
        "name":"Spongebob Porn Comics",
        "value":"spongebob-porn-comics"
        },
        {
        "name":"Spy x Family Porn",
        "value":"p0rn-spy-x-family"
        },
        {
        "name":"Squirrel Girl Porn",
        "value":"squirrel-girl-porn"
        },
        {
        "name":"Star Butterfly Porn",
        "value":"star-butterfly-porn"
        },
        {
        "name":"Star Trek",
        "value":"star-trek"
        },
        {
        "name":"Star vs The Forces of Evil",
        "value":"porn-comic-star-vs-the-forces-of-evil"
        },
        {
        "name":"Star Wars",
        "value":"star-wars"
        },
        {
        "name":"Starfire",
        "value":"starfire"
        },
        {
        "name":"Steven Universe Porn Comics",
        "value":"steven-universe-porn_comics1"
        },
        {
        "name":"StormFedeR",
        "value":"stormfeder-porn"
        },
        {
        "name":"Street Fighter Porn",
        "value":"street-fighter-porn-comix1"
        },
        {
        "name":"Strong Bana",
        "value":"strong-bana"
        },
        {
        "name":"StrongBana",
        "value":"strongbana-porn"
        },
        {
        "name":"Suguha Hentai",
        "value":"suguha-hentai"
        },
        {
        "name":"Summer Hentai",
        "value":"summer-hentai"
        },
        {
        "name":"Super Melons",
        "value":"super-melons"
        },
        {
        "name":"Supergirl",
        "value":"supergirl"
        },
        {
        "name":"Superman",
        "value":"superman-x"
        },
        {
        "name":"Taboolicious",
        "value":"taboolicious"
        },
        {
        "name":"Taimanin Asagi",
        "value":"taimanin-asagi"
        },
        {
        "name":"Tatsumaki Hentai",
        "value":"tatsumaki-hentai-one-punch-mam"
        },
        {
        "name":"Tawna",
        "value":"tawna"
        },
        {
        "name":"Teacher",
        "value":"teacher"
        },
        {
        "name":"Teen",
        "value":"teen"
        },
        {
        "name":"Teenage Mutant Ninja Turtles Porn Comics",
        "value":"teenage-mutant-ninja-turtles-porn-comics"
        },
        {
        "name":"Teens",
        "value":"teens"
        },
        {
        "name":"Tenn",
        "value":"tenn"
        },
        {
        "name":"Tentacles",
        "value":"tentacles"
        },
        {
        "name":"Tenten Hentai",
        "value":"tenten-hentai"
        },
        {
        "name":"the addams family",
        "value":"the-addams-family"
        },
        {
        "name":"The Amazing Digital Circus",
        "value":"the-amazing-digital-circus"
        },
        {
        "name":"The Amazing world of Gumball Porn Comics",
        "value":"amazing-world-of-gumball-porn-comixs"
        },
        {
        "name":"The Arthman",
        "value":"tthe-arthman"
        },
        {
        "name":"the elder scrolls",
        "value":"the-elder-scrolls"
        },
        {
        "name":"The Fairly OddParents Porn",
        "value":"porn-comics-the-fairly-oddparents-xx"
        },
        {
        "name":"The Grim Adventures of Billy & Mandy Porn",
        "value":"the-grim-adventures-of-billy-mandy-porn"
        },
        {
        "name":"The grim adventures of Billy and Mandy",
        "value":"the-grim-adventures-of-billy-and-mandy"
        },
        {
        "name":"The Idolmaster",
        "value":"the-idolmaster"
        },
        {
        "name":"The Incredibles Porn",
        "value":"the-incredibles-porn_comics"
        },
        {
        "name":"The Jetsons",
        "value":"the-jetsons"
        },
        {
        "name":"The Last Airbender",
        "value":"the-last-airbender"
        },
        {
        "name":"The Last of Us",
        "value":"the-last-of-us"
        },
        {
        "name":"The Legend of Zelda Hentai",
        "value":"the-legend-of-zelda-p"
        },
        {
        "name":"The Loud House",
        "value":"porn-comic-the-loud-house-sex"
        },
        {
        "name":"The Owl House",
        "value":"porn-comics-the-owl-house"
        },
        {
        "name":"the powerpuff girls",
        "value":"the-powerpuff-girls"
        },
        {
        "name":"The proud family",
        "value":"the-proud-family"
        },
        {
        "name":"The Ring",
        "value":"the-ring"
        },
        {
        "name":"the seven deadly sins",
        "value":"the-seven-deadly-sins"
        },
        {
        "name":"The Summoning Porn",
        "value":"the-summoning-porn"
        },
        {
        "name":"The Sword In The Stone Porn Comics",
        "value":"the-sword-in-the-stone-porn-comics"
        },
        {
        "name":"The Walking Dead",
        "value":"the-walking-dead"
        },
        {
        "name":"The Witcher",
        "value":"the-witcher"
        },
        {
        "name":"Thicc",
        "value":"thicc-xxx"
        },
        {
        "name":"Three Houses",
        "value":"three-houses"
        },
        {
        "name":"Tifa Hentai",
        "value":"tifa-hentai"
        },
        {
        "name":"To Love-Ru Hentai",
        "value":"to-love-ru-hentai"
        },
        {
        "name":"Tomb Raider",
        "value":"tomb-raider"
        },
        {
        "name":"Tomgirl",
        "value":"tomgirl"
        },
        {
        "name":"Toph Hentai",
        "value":"toph-hentai"
        },
        {
        "name":"Total Drama",
        "value":"total-drama-porn-comic"
        },
        {
        "name":"Totally Spies",
        "value":"porn-comics-totally-spies"
        },
        {
        "name":"Touhou Project Hentai",
        "value":"touhou-project-hentaix"
        },
        {
        "name":"Tracy Scops",
        "value":"tracy-scops"
        },
        {
        "name":"Transformers Porn",
        "value":"transformers-porn"
        },
        {
        "name":"Tricia Hentai",
        "value":"tricia-hentai"
        },
        {
        "name":"Tsunade Hentai",
        "value":"tsunade_hentai"
        },
        {
        "name":"Tsuyu Asui Hentai",
        "value":"tsuyu-asui-hentai"
        },
        {
        "name":"Umbreon",
        "value":"umbreon"
        },
        {
        "name":"Uncensored",
        "value":"uncensored"
        },
        {
        "name":"Undertale",
        "value":"undertale"
        },
        {
        "name":"Undyne",
        "value":"undyne"
        },
        {
        "name":"Uraraka Hentai",
        "value":"uraraka-hentai-xxx"
        },
        {
        "name":"Urbosa",
        "value":"urbosa"
        },
        {
        "name":"Vados Hentai",
        "value":"vados-hentai"
        },
        {
        "name":"Vanilla",
        "value":"vanilla"
        },
        {
        "name":"Velma Hentai",
        "value":"velma-hentai"
        },
        {
        "name":"VerComicsPorno",
        "value":"vercomicsporno"
        },
        {
        "name":"Vicky",
        "value":"vicky"
        },
        {
        "name":"Videl Hentai",
        "value":"videl-hentai1"
        },
        {
        "name":"violet parr",
        "value":"violet-parr"
        },
        {
        "name":"Wakfu Porn",
        "value":"wakfu-porn"
        },
        {
        "name":"Wander Over Yonder",
        "value":"wander-over-yonder"
        },
        {
        "name":"Warcraft",
        "value":"warcraft"
        },
        {
        "name":"Warhammer porn",
        "value":"warhammer-porn"
        },
        {
        "name":"Wendy",
        "value":"wendy"
        },
        {
        "name":"Widowmaker",
        "value":"widowmaker"
        },
        {
        "name":"Willow",
        "value":"willow"
        },
        {
        "name":"Wonder Woman",
        "value":"xxx-wonder-woman"
        },
        {
        "name":"World of Warcraft",
        "value":"world-of-warcraft"
        },
        {
        "name":"WoW",
        "value":"wow"
        },
        {
        "name":"x-men",
        "value":"x-men"
        },
        {
        "name":"Xayah",
        "value":"xayah"
        },
        {
        "name":"Xenoblade Chronicles 2 Hentai",
        "value":"xenoblade-chronicles-2-hentaix"
        },
        {
        "name":"Xierra099",
        "value":"xierra099"
        },
        {
        "name":"Y3DF",
        "value":"y3df"
        },
        {
        "name":"Yamato Hentai",
        "value":"yamato-hentai"
        },
        {
        "name":"Yu Takeyama Hentai",
        "value":"yu-takeyama-hentai"
        },
        {
        "name":"Yu-Gi-Oh Hentai",
        "value":"yu-gi-oh-entai"
        },
        {
        "name":"Yuri",
        "value":"yuri-hentai"
        },
        {
        "name":"Zac",
        "value":"zac"
        },
        {
        "name":"Zelda Hentai",
        "value":"zelda-hentai"
        },
        {
        "name":"Zenless Zone Zero porn",
        "value":"zenless-zone-zero-porn"
        },
        {
        "name":"Zoe",
        "value":"zoe"
        },
        {
        "name":"Zootopia",
        "value":"zootopia"
        }
        ],
        "default":"3d-porn-comics-xxx"
        }
        ]
        return [SourceFilter (**item )for item in data ]

    name ='kingcomix_en'
    display_name ='KingComiX'
    base_url ='https://kingcomix.com'
    language ='en'
    requests_per_minute =60 


SOURCE =GeneratedGenericSource

"""Puente de contrato para adaptadores que conservan metodos v3."""

import inspect
from collections.abc import Mapping
from typing import Any

from nyanko_api.sources.contract import Paginated, SourceFilter, SourcePreference

_PAGE_SIZE = 20


def _parameters(method: Any) -> Mapping[str, Any]:
    return inspect.signature(method).parameters


def _arguments(method: Any, page: int, filters: Mapping[str, Any] | None) -> dict[str, Any]:
    parameters = _parameters(method)
    arguments: dict[str, Any] = {}
    if "page" in parameters:
        arguments["page"] = page
    if "filters" in parameters:
        arguments["filters"] = filters
    if "limit" in parameters:
        # Un metodo v3 sin `page` solo se controla por `limit`: se pide el
        # acumulado hasta la pagina solicitada y luego se recorta el tramo. El
        # elemento extra es el sondeo que distingue "no hay mas" de "justo cabia".
        arguments["limit"] = _PAGE_SIZE if "page" in parameters else page * _PAGE_SIZE + 1
    return arguments


def _unwrap(value: Any) -> tuple[list[Any], bool | None]:
    """Normaliza un retorno v3 a ``(items, has_more)``; ``None`` si no lo declara."""
    if isinstance(value, Paginated):
        return list(value.items), value.has_more
    if isinstance(value, dict):
        declared = value.get("has_more", value.get("has_next_page"))
        items = value.get("items", value.get("results", []))
        return list(items or []), None if declared is None else bool(declared)
    return list(value or []), None


def _paginated(value: Any, has_more: bool) -> Paginated:
    items, declared = _unwrap(value)
    if declared is not None:
        has_more = declared
    return Paginated(items=items, has_more=has_more and bool(items))


def _window(value: Any, page: int) -> Paginated:
    """Pagina en el cliente un metodo v3 que devuelve el acumulado de una vez."""
    items, declared = _unwrap(value)
    start = (page - 1) * _PAGE_SIZE
    window = items[start : start + _PAGE_SIZE]
    has_more = len(items) > start + _PAGE_SIZE if declared is None else declared
    return Paginated(items=window, has_more=has_more and bool(window))


def _consumes_filters(legacy_source: type) -> bool:
    return any(
        "filters" in _parameters(method)
        for name in ("search", "browse")
        if callable(method := getattr(legacy_source, name, None))
    )


def _options(options: Any) -> list[tuple[str, str]] | None:
    if options is None:
        return None
    return [
        (str(option.get("value", "")), str(option.get("name", "")))
        if isinstance(option, dict)
        else (str(option[0]), str(option[1]))
        for option in options
    ]


def _filters(values: Any) -> list[SourceFilter]:
    return [
        SourceFilter(
            id=value.id,
            name=value.name,
            type="multi_select" if value.type == "group" else value.type,
            options=_options(value.options),
            default=[] if value.type == "group" and not isinstance(value.default, list) else value.default,
        )
        for value in values
    ]


def _preferences(values: Any) -> list[SourcePreference]:
    return [
        SourcePreference(
            id=value.id,
            name=value.name,
            type=value.type,
            options=_options(value.options),
            default=value.default,
        )
        for value in values
    ]


def adapt_source(legacy_source: type) -> type:
    # Un filtro que ningun metodo v3 acepta no se anuncia: la UI mostraria
    # controles que el adaptador descarta en silencio.
    publishes_filters = _consumes_filters(legacy_source)

    class SourceV4(legacy_source):
        async def get_filters(self) -> list[SourceFilter]:
            getter = getattr(super(), "get_filters", None)
            if not getter or not publishes_filters:
                return []
            values = getter()
            if inspect.isawaitable(values):
                values = await values
            return _filters(values)

        def get_preferences(self) -> list[SourcePreference]:
            getter = getattr(super(), "get_preferences", None)
            return _preferences(getter()) if getter else []

        async def search(
            self,
            query: str,
            page: int = 1,
            filters: Mapping[str, Any] | None = None,
        ) -> Paginated:
            method = super().search
            result = await method(query, **_arguments(method, page, filters))
            if "page" in _parameters(method):
                return _paginated(result, True)
            return _window(result, page)

        async def browse(
            self,
            kind: str,
            page: int = 1,
            filters: Mapping[str, Any] | None = None,
        ) -> Paginated:
            method = super().browse
            return _paginated(await method(kind, **_arguments(method, page, filters)), True)

    SourceV4.__name__ = legacy_source.__name__
    SourceV4.__qualname__ = legacy_source.__qualname__
    return SourceV4

SOURCE = adapt_source(SOURCE)
