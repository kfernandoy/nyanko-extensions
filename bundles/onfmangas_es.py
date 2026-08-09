"""Implementación común del tema Madara para bundles Nyanko Source v4."""

from __future__ import annotations 

import ast 
import base64 
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
SourcePage ,
SourcePageContent ,
SourceFilter ,
SourcePreference ,
SourceSeries ,
)
from nyanko_api .sources .errors import SourceNotFoundError 


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


def _image_url (node :_Node ,base_url :str )->str :
    for key in (
    "data-lm-orig-src",
    "data-src",
    "data-lazy-src",
    "data-cfsrc",
    "data-manga-src",
    "src",
    ):
        if node .attrs .get (key ):
            return urljoin (base_url ,node .attrs [key ].strip ())
    candidates =[
    item .strip ().split ()[0 ]
    for item in node .attrs .get ("srcset","").split (",")
    if item .strip ()
    ]
    return urljoin (base_url ,candidates [-1 ])if candidates else ""


class MadaraSource :
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
            response =await self ._request (
            "GET",
            f"{self .base_url }/{self .manga_substring .strip ('/')}/{suffix }",
            params ={"m_orderby":"views"if kind =="popular"else "latest"},
            )
        response .raise_for_status ()
        root =_parse_html (response .text )
        if self .load_more =="auto":
            self ._load_more_detected =any (
            node .tag =="nav"and node .has_class ("navigation-ajax")
            for node in root .descendants ()
            )
        return self ._series_from_root (root ,("page-item-detail","manga__item"))

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
        if not items and holder is not None :
            if self .use_new_chapter_endpoint :
                response =await self ._request ("POST",f"{series_url .rstrip ('/')}/ajax/chapters")
            else :
                response =await self ._request (
                "POST",
                f"{self .base_url }/wp-admin/admin-ajax.php",
                data ={"action":"manga_get_chapters","manga":holder .attrs .get ("data-id","")},
                )
                if getattr (response ,"status_code",200 )==400 :
                    response =await self ._request ("POST",f"{series_url .rstrip ('/')}/ajax/chapters")
            response .raise_for_status ()
            items =self ._chapter_nodes (_parse_html (response .text ))
            if not items :
                items =self ._fallback_chapter_nodes (_parse_html (response .text ))

        result :list [SourceChapter ]=[]
        for item in items :
            anchor =_first (item ,lambda node :node .tag =="a"and bool (node .attrs .get ("href")))
            if anchor is None :
                continue 
            title =anchor .text ().strip ()
            chapter_url =urljoin (series_url ,anchor .attrs ["href"]).split ("?style=paged",1 )[0 ]
            if self .chapter_url_suffix and not chapter_url .endswith (self .chapter_url_suffix ):
                chapter_url +=self .chapter_url_suffix 
            match =re .search (r"(?:chapter|cap(?:í|i)tulo|ch)[^\d]*(\d+(?:\.\d+)?)",title ,re .I )
            result .append (
            SourceChapter (
            source_id =chapter_url ,
            title =title or "Capítulo",
            series_id =series_id ,
            source_name =self .name ,
            number =float (match .group (1 ))if match else None ,
            )
            )
        return result 

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
        if reading is not None :
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
        if self .pages_profile =="https":
            urls =[url .replace ("http://","https://",1 )for url in urls ]
        elif self .pages_profile =="skip_placeholder"and urls :
            if urls [0 ].split ("?",1 )[0 ].endswith ("/1-000001.jpg"):
                urls =urls [1 :]
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
        if isinstance (page ,SourcePage ):
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
            image =_first (item ,lambda node :node .tag =="img")
            result .append (
            SourceSeries (
            source_id =source_id ,
            title =title ,
            source_name =self .name ,
            cover_url =_image_url (image ,self .base_url )if image else None ,
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
                image =_first (anchor ,lambda node :node .tag =="img")
                result .append (
                SourceSeries (
                source_id =source_id ,
                title =title ,
                source_name =self .name ,
                cover_url =_image_url (image ,self .base_url )if image else None ,
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

    async def _request (self ,method :str ,url :str ,**kwargs :Any )->Any :
        if self .fetcher is None :
            raise SourceNotFoundError (f"{self .display_name } no tiene fetcher inyectado")
        return await self .fetcher .request (method ,url ,**kwargs )


"""Adaptador de ONF MANGAS: catalogo HTML y payloads en hexadecimal."""

_ONF_GENRES =(
('0','Todas las categorías'),
('44','4-venida'),
('38','A todo color'),
('14','Acción'),
('13','Adaptación'),
('87','Adulto'),
('43','Amor de chicas'),
('61','Amor de chicos'),
('66','Animales'),
('71','Antología'),
('83','Apocalipsis'),
('42','Arreglarlo'),
('7','Artes marciales'),
('54','Autoeditado'),
('15','Aventura'),
('33','Cambio de sexo'),
('64','Chicas magicas'),
('22','Chicas monstruo'),
('52','Ciencia ficción'),
('70','Cocinando'),
('80','Color del abanico'),
('69','Color oficial'),
('1','Comedia'),
('18','Cómic web'),
('97','Crimen'),
('90','Culinario'),
('92','Cultivación'),
('84','Cyberpunk'),
('12','Delincuentes'),
('76','Delito'),
('25','Demonios'),
('68','Deportes'),
('73','Dominó chino'),
('65','Doujinshi'),
('2','Drama'),
('3','Ecchi'),
('75','Extraterrestres'),
('16','Fantasía'),
('29','Fantasmas'),
('49','Filosófico'),
('85','Gore'),
('8','Harem'),
('81','Harem inverso'),
('82','Hentai'),
('28','Histórico'),
('51','Horror'),
('40','Incesto'),
('55','Intercambio de género'),
('11','Isekai'),
('88','Jefe/Empleado'),
('58','Juegos de vídeo'),
('72','Juegos tradicionales'),
('35','Loli'),
('46','Mafia'),
('17','Magia'),
('63','Meca'),
('53','Mecha'),
('50','Médico'),
('36','Militar'),
('31','Misterio'),
('24','Monstruos'),
('78','Música'),
('79','Ninja'),
('95','Niños'),
('62','Policía'),
('59','Post-apocalíptico'),
('21','Premiado'),
('26','Psicológico'),
('98','Realeza'),
('57','Realidad virtual'),
('6','Recuentos de la vida'),
('9','Reencarnación'),
('4','Romance'),
('56','Samurai'),
('27','Sangre'),
('32','Shota'),
('94','Shoujo Ai'),
('96','Smut'),
('23','Sobrenatural'),
('74','Superhéroe'),
('30','Supervivencia'),
('48','Suspense'),
('67','Suspenso'),
('37','Tira larga'),
('20','Trabajadores de oficina'),
('47','Tragedia'),
('34','Travestismo'),
('39','Un trago'),
('41','Vampiros'),
('93','Venganza'),
('19','Viaje en el tiempo'),
('5','Vida escolar'),
('91','Vida Laboral'),
('86','Videojuegos'),
('45','Villana'),
('10','Violencia sexual'),
('77','Wuxia'),
('89','Yaoi'),
('60','Zombis'),
)

_ONF_HEX_CHAPTERS =re .compile (r'const\s+_hex\s*=\s*"([0-9a-fA-F]*)"')
_ONF_HEX_PAGES =re .compile (r'const\s+_hexP\s*=\s*"([0-9a-fA-F]*)"')


class OnfMangasSource (MadaraSource ):
    """Capitulos y paginas viajan como JSON en hexadecimal dentro de un script."""

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("tab","Categoría principal","select",[
        ("general","General"),("yuri","GL / Yuri"),
        ("yaoi","BL / Yaoi"),("doujinshi","Doujinshis"),
        ],"general"),
        SourceFilter ("genero","Género","select",list (_ONF_GENRES ),"0"),
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind =="popular":
            response =await self ._fetch ("GET",f"{self .base_url }/populares.php")
            root =_parse_html (response .text )
            base =str (response .url )or self .base_url 
            items :list [SourceSeries ]=[]
            for anchor in root .descendants ("a"):
                if not (anchor .has_class ("pop-podium-card")or anchor .has_class ("pop-card")):
                    continue 
                heading =_first (
                anchor ,
                lambda node :node .has_class ("pop-podium-name")or node .has_class ("pop-name"),
                )
                href =anchor .attrs .get ("href","")
                if heading is None or not heading .text ().strip ()or not href :
                    continue 
                image =_first (anchor ,lambda node :node .tag =="img")
                items .append (
                SourceSeries (
                source_id =urlparse (urljoin (base ,href )).path .lstrip ("/"),
                title =heading .text ().strip (),
                source_name =self .name ,
                cover_url =urljoin (base ,image .attrs .get ("src",""))if image is not None else None ,
                web_url =urljoin (base ,href ),
                )
                )
            return {"items":items ,"has_more":False }
        if kind =="latest":
            return await self ._grid ([
            ("tab","general"),("genero","0"),("q",""),("page",str (page )),
            ])
        return {"items":[],"has_more":False }

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        values =filters or {}
        params :list [tuple [str ,str ]]=[
        ("q",query ),("page",str (page )),("tab",str (values .get ("tab")or "general")),
        ]
        genre =str (values .get ("genero")or "0")
        # El sitio espera "generos[0]"; la categoria "0" no se envia.
        if genre !="0":
            params .append (("generos[0]",genre ))
        return await self ._grid (params )

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._fetch ("GET",urljoin (f"{self .base_url }/",series_id ))
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        heading =_first (root ,lambda node :node .has_class ("manga-title"))
        if heading is None or not heading .text ().strip ():
            raise SourceNotFoundError (f"{self .display_name }: ficha sin titulo")
        author =_first (root ,lambda node :node .has_class ("author-link"))
        summary =_first (root ,lambda node :node .has_class ("manga-description"))
        poster =_first (root ,lambda node :node .has_class ("manga-poster"))
        badges =[
        node 
        for holder in root .descendants ("div")
        if holder .has_class ("manga-meta")
        for node in holder .descendants ("span")
        ]
        text =badges [-1 ].text ().casefold ()if badges else ""
        return SourceSeries (
        source_id =series_id ,
        title =heading .text ().strip (),
        source_name =self .name ,
        cover_url =urljoin (base ,poster .attrs .get ("src",""))if poster is not None else None ,
        description =(summary .text ().strip ()if summary is not None else None )or None ,
        author =(author .text ().strip ()if author is not None else None )or None ,
        status ="ongoing"if "emisión"in text else "completed"if "finalizado"in text else None ,
        content_tags =tuple (
        value 
        for node in root .descendants ()
        if node .has_class ("genre-tag")and (value :=node .text ().strip ())
        ),
        web_url =urljoin (f"{self .base_url }/",series_id ),
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._fetch ("GET",urljoin (f"{self .base_url }/",series_id ))
        entries =self ._hex_payload (response .text ,_ONF_HEX_CHAPTERS )
        # El sitio ordena en el cliente: numero descendente y luego fecha.
        entries .sort (
        key =lambda item :(self ._number (item ),str (item .get ("fecha_subida")or "")),
        reverse =True ,
        )
        result :list [SourceChapter ]=[]
        for item in entries :
            result .append (self ._chapter (item ,None ,series_id ))
            for other in item .get ("otras_versiones")or []:
                if isinstance (other ,dict ):
                    result .append (self ._chapter (other ,item ,series_id ))
        return result 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        response =await self ._fetch ("GET",urljoin (f"{self .base_url }/",chapter_id ))
        result :list [SourcePage ]=[]
        for index ,item in enumerate (self ._hex_payload (response .text ,_ONF_HEX_PAGES )):
            source =str (item .get ("src")or "")
            if not source :
                continue 
            fallback =str (item .get ("fallback")or "").strip ()
            result .append (
            SourcePage (
            source_id =f"{source }#fallback={fallback }"if fallback else source ,
            chapter_id =chapter_id ,
            index =index ,
            filename =urlparse (source ).path .rsplit ("/",1 )[-1 ]or f"{index }.jpg",
            source_name =self .name ,
            )
            )
        return result 

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        url =page .source_id if isinstance (page ,SourcePage )else str (page )
        source ,_ ,fragment =url .partition ("#fallback=")
        try :
            return await super ().page_bytes (source if fragment else url )
        except Exception :
            if not fragment :
                raise 
                # El origen principal falla a menudo; el sitio publica un respaldo.
            return await super ().page_bytes (fragment )

            # -------------------------------------------------------------- internals
    async def _grid (self ,params :list [tuple [str ,str ]])->dict :
        response =await self ._fetch ("GET",f"{self .base_url }/mangas.php",params =params )
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        items :list [SourceSeries ]=[]
        for grid in root .descendants ("div"):
            if not grid .has_class ("manga-grid"):
                continue 
            for card in grid .descendants ("div"):
                if not card .has_class ("manga-card"):
                    continue 
                heading =_first (card ,lambda node :node .has_class ("manga-title"))
                anchor =_first (card ,lambda node :node .tag =="a")
                if heading is None or anchor is None :
                    continue 
                title ,href =heading .text ().strip (),anchor .attrs .get ("href","")
                if not title or not href :
                    continue 
                image =next (
                (
                node 
                for holder in card .descendants ()
                if holder .has_class ("card-cover")
                for node in holder .descendants ("img")
                ),
                None ,
                )
                items .append (
                SourceSeries (
                source_id =urlparse (urljoin (base ,href )).path .lstrip ("/"),
                title =title ,
                source_name =self .name ,
                cover_url =urljoin (base ,image .attrs .get ("src",""))if image is not None else None ,
                web_url =urljoin (base ,href ),
                )
                )
        has_more =any (
        anchor .has_class ("page-btn")and "siguiente"in anchor .text ().casefold ()
        for holder in root .descendants ()
        if holder .has_class ("pagination")
        for anchor in holder .descendants ("a")
        )
        return {"items":items ,"has_more":has_more }

    async def _fetch (self ,method :str ,url :str ,**kwargs :Any )->Any :
        response =await self ._request (method ,url ,**kwargs )
        response .raise_for_status ()
        if "Verificando"in (response .text or "")[:8192 ]:
        # El reto es un script que fija una cookie; aqui no hay motor JS.
            raise ValueError (
            f"{self .display_name } está pidiendo una verificación: ábrelo en WebView y vuelve a intentarlo",
            )
        return response 

    def _chapter (self ,item :dict ,parent :dict |None ,series_id :str )->SourceChapter :
        number =item .get ("numero")or (parent or {}).get ("numero")
        title =(
        item .get ("titulo_str")
        or (parent or {}).get ("titulo_str")
        or (f"Capítulo {number }"if number else "Capítulo sin número")
        )
        groups =[
        str (group .get ("nombre")or "")
        for group in item .get ("grupos_list")or []
        if isinstance (group ,dict )
        ]
        stamp =(item if parent is None else parent ).get ("fecha_subida")
        return SourceChapter (
        source_id =str (item .get ("url")or "").lstrip ("/"),
        title =str (title ),
        series_id =series_id ,
        source_name =self .name ,
        number =self ._number (item ),
        language =self .language ,
        scanlator =" & ".join (groups ),
        uploaded_at =self ._date (stamp ),
        )

    @staticmethod 
    def _hex_payload (text :str ,pattern :Any )->list [dict ]:
        found =pattern .search (text or "")
        if not found or len (found .group (1 ))%2 :
            return []
        try :
            decoded =bytes .fromhex (found .group (1 )).decode ("utf-8")
            value =json .loads (decoded )
        except (ValueError ,UnicodeDecodeError ):
            return []
        return [item for item in value or []if isinstance (item ,dict )]

    @staticmethod 
    def _number (item :dict )->float :
        try :
            return float (item .get ("numero"))
        except (TypeError ,ValueError ):
            return 0.0 

    @staticmethod 
    def _date (value :Any )->str |None :
        from datetime import datetime 

        if not value :
            return None 
        try :
            return datetime .strptime (str (value ),"%Y-%m-%d %H:%M:%S").isoformat ()
        except ValueError :
            return None 


class GeneratedOnfMangasSource (OnfMangasSource ):
    name ='onfmangas_es'
    display_name ='ONF MANGAS'
    base_url ='https://onfmangas.com'
    language ='es'
    requests_per_minute =60 
    content_warning ='mixed'
    extra_headers ={
    'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0',
    'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language':'en-US,en;q=0.9',
    'Sec-Fetch-Site':'none',
    }
    image_headers ={'Referer':'https://onfmangas.com/'}


SOURCE =GeneratedOnfMangasSource

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
