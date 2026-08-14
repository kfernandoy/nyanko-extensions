from __future__ import annotations

"""Implementación común del tema Madara para bundles Nyanko Source v4."""

 

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


class FuenteBaseSource :
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

"""Implementación HTML común de GalleryAdults."""

import json 
import re 
from urllib .parse import urljoin ,urlparse 



class GalleryAdultsSource (FuenteBaseSource ):
    manga_language =""
    profile =""

    @staticmethod 
    def _es_enlace_de_galeria (href :str )->bool :
        """`True` si el href apunta a la ficha de una obra.

        Los sitios del tema no comparten prefijo: imhentai, hentaiera, hentaizap y
        hentaienvy usan `/gallery/123/`, mientras que asmhentai y nhentai.xxx usan
        `/g/123/`. Filtrar solo por `/gallery/` dejaba a estos dos cayendo al enlace
        de categoria, asi que el listado colapsaba a 2 tarjetas repetidas.
        """
        ruta =urlparse (href ).path 
        return bool (re .search (r"/(?:gallery|g|view)/\d+",ruta ))

    @staticmethod 
    def _es_bandera (node )->bool :
        """`True` si el <img> es la banderita de idioma de la tarjeta, no la portada.

        Cada tarjeta abre con `<div class="cat_flag"><img class="thumb_flag"
        src="/images/esp.png">` ANTES del `<div class="inner_thumb">` que lleva la
        portada real. Coger el primer <img> del contenedor devolvia esa bandera, asi
        que el listado entero salia con `esp.png` / `uk_usa.png` de portada.

        Se descarta por clase y por ruta: `thumb_flag` es la clase del tema y
        `/images/` es la carpeta de assets estaticos del sitio, mientras que las
        portadas viven siempre en el CDN (`m10.imhentai.xxx/...`).
        """
        clases =node .attrs .get ("class","").split ()
        if "thumb_flag"in clases :
            return True 
        padre =node .parent 
        saltos =0 
        while padre is not None and saltos <2 :
            if "cat_flag"in padre .attrs .get ("class","").split ():
                return True 
            padre =padre .parent 
            saltos +=1 
        for clave in ("data-src","data-lazy-src","src"):
            valor =node .attrs .get (clave ,"").strip ()
            # El `src` inicial es un SVG en data: URI (el placeholder del lazy-load);
            # no delata nada, asi que solo se mira la ruta de assets del sitio.
            if valor .startswith ("data:"):
                continue 
            if valor and "/images/"in urlparse (valor ).path :
                return True 
        return False 

    def _portada (self ,item ,base :str )->str |None :
        image =_first (
        item ,
        lambda node :node .tag =="img"and not self ._es_bandera (node ),
        )
        # Si en la tarjeta SOLO habia banderas se cae al comportamiento anterior: es
        # preferible una portada equivocada a quedarse sin ninguna.
        image =image or _first (item ,lambda node :node .tag =="img")
        return _image_url (image ,base )if image else None 

    def _series (self ,html :str ,base :str )->list [SourceSeries ]:
        root =_parse_html (html )
        classes ={"thumb","preview_item","gallery_item"}
        result :list [SourceSeries ]=[]
        for item in (
        node 
        for node in root .descendants ()
        if classes .intersection (node .attrs .get ("class","").split ())
        ):
        # El PRIMER <a> de la tarjeta es el de la categoria (`/category/doujinshi/`),
        # no el de la galeria: quedarse con el hacia que las 25 tarjetas de la pagina
        # compartieran titulo ("Doujinshi", "Western") y el dedupe final las colapsara
        # a 4 o 5. Se prefiere el enlace que apunta a una galeria.
            enlaces =[
            node for node in item .descendants ("a")if node .attrs .get ("href")
            ]
            link =next (
            (node for node in enlaces if self ._es_enlace_de_galeria (node .attrs ["href"])),
            # Sin enlace de galeria reconocible se descarta la tarjeta: caer al
            # primer <a> devolvia la categoria o el idioma, y el dedupe final
            # colapsaba la pagina entera a 2-4 entradas falsas.
            None ,
            )
            caption =_first (
            item ,
            lambda node :any (
            name in node .attrs .get ("class","").split ()
            # `gallery_title` es el titulo real de la obra; `gallery_cat` es la
            # categoria y se descarta a proposito.
            for name in ("gallery_title","caption","title","tag_name")
            )
            and node .text (),
            )
            title =caption .text ()if caption else link .text ()if link else ""
            if link and title :
                source_id =urljoin (base ,link .attrs ["href"])
                result .append (
                SourceSeries (
                source_id =source_id ,
                title =title ,
                source_name =self .name ,
                cover_url =self ._portada (item ,base ),
                web_url =source_id ,
                )
                )
        return list ({item .source_id :item for item in result }.values ())

    async def _catalog (self ,popular :bool ,page :int )->list [SourceSeries ]:
        path =self .base_url 
        if self .manga_language :
            path +=f"/language/{self .manga_language }"
        if popular :
            path +="/popular"
            # La barra final NO es opcional: `/language/spanish/popular` devuelve 404 y solo
            # `/language/spanish/popular/` responde el listado. El engine la omitia, asi que
            # el catalogo entero salia vacio en las 8 variantes.
        if not path .endswith ("/"):
            path +="/"
        response =await self ._request ("GET",path ,params ={"page":max (page ,1 )})
        response .raise_for_status ()
        return self ._series (response .text ,path )

    async def search (self ,query :str ,limit :int =20 )->list [SourceSeries ]:
        response =await self ._request (
        "GET",
        f"{self .base_url }/search/",
        params ={"q":query .strip (),"key":query .strip (),"page":1 },
        )
        response .raise_for_status ()
        return self ._series (response .text ,self .base_url )[:limit ]

    async def browse (self ,kind :str ,page :int =1 )->list [SourceSeries ]:
        if kind not in {"popular","latest"}:
            return []
        return await self ._catalog (kind =="popular",page )

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        """Ficha de la galeria.

        El `details` heredado de Madara busca `post-title`, `summary_image` y
        `post-content_item`, que este tema no emite: la ficha salia entera vacia
        (sin portada, sin autor, sin tags).

        Los seis sitios del tema tienen markups distintos para la lista de etiquetas
        (`span.tags_text` + `a.tag` en imhentai, `div.tags` + `span.badge` en
        asmhentai, `span.info_txt` + `a.gp_btn_tag` en hentaizap, `li.tags` +
        `span.tag_name` en nhentai.xxx), asi que NO se clasifica por clase CSS sino
        por el prefijo del href, que si es uniforme: `/tag/`, `/artist/`, `/parody/`,
        `/category/`. El contador de cada etiqueta va en un `<span>` hijo y se
        descuenta del texto para no acabar con "nakadashi 225889".
        """
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )

        titulo =_first (root ,lambda node :node .tag =="h1")
        subtitulo =_first (root ,lambda node :node .tag =="p"and node .has_class ("subtitle"))
        # La portada de la ficha es `.../cover.jpg`; el resto de <img> son las
        # miniaturas `1t.jpg`, `2t.jpg`... del previsualizador.
        portada =_first (
        root ,
        lambda node :node .tag =="img"
        and not self ._es_bandera (node )
        and "cover"in _image_url (node ,base ).rsplit ("/",1 )[-1 ],
        )
        portada =portada or _first (
        root ,
        lambda node :node .tag =="img"
        and node .has_class ("lazy")
        and not self ._es_bandera (node ),
        )

        def campo (*prefijos :str )->list [str ]:
            valores :list [str ]=[]
            for enlace in root .descendants ("a"):
                ruta =urlparse (enlace .attrs .get ("href","")).path 
                if not any (ruta .startswith (f"/{prefijo }/")for prefijo in prefijos ):
                    continue 
                texto =enlace .text ()
                for hijo in enlace .descendants ("span"):
                # Solo se descuentan los <span> HOJA: en asmhentai el contador va
                # dentro de `<span class="badge tag">`, que envuelve tambien al
                # nombre, y recortar el envoltorio dejaba la etiqueta vacia.
                    if any (True for _ in hijo .descendants ("span")):
                        continue 
                    clases =hijo .attrs .get ("class","").split ()
                    if any (
                    marca in clase 
                    for clase in clases 
                    for marca in ("badge","count")
                    ):
                        texto =texto .replace (hijo .text ()," ")
                if texto :=" ".join (texto .split ()):
                    valores .append (texto )
            return valores 

        artistas =campo ("artist")
        etiquetas =[*campo ("tag"),*campo ("category"),*campo ("parody")]
        return SourceSeries (
        source_id =series_id ,
        title =titulo .text ().strip ()if titulo else (
        series .title if isinstance (series ,SourceSeries )
        else series_id .rstrip ("/").rsplit ("/",1 )[-1 ]
        ),
        source_name =self .name ,
        cover_url =_image_url (portada ,base )if portada else (
        series .cover_url if isinstance (series ,SourceSeries )else None 
        ),
        description =subtitulo .text ().strip ()if subtitulo else None ,
        author =", ".join (artistas )or None ,
        artist =", ".join (artistas )or None ,
        content_tags =tuple (dict .fromkeys (etiquetas )),
        web_url =base ,
        metadata =series .metadata if isinstance (series ,SourceSeries )else {},
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else series 
        return [
        SourceChapter (
        source_id =series_id ,
        title ="Chapter",
        series_id =series_id ,
        source_name =self .name ,
        )
        ]

    @staticmethod 
    def _inputs (root )->dict [str ,str ]:
        return {
        node .attrs ["id"]:node .attrs .get ("value","")
        for node in root .descendants ("input")
        if node .attrs .get ("id")
        }

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else chapter 
        response =await self ._request ("GET",chapter_id )
        response .raise_for_status ()
        root =_parse_html (response .text )
        inputs =self ._inputs (root )
        scripts ="\n".join (node .text ()for node in root .descendants ("script"))
        encoded =re .search (r"\$\.parseJSON\('(.+?)'\)",scripts ,re .DOTALL )
        urls :list [str ]=[]
        if encoded :
            payload =json .loads (encoded .group (1 ).encode ().decode ("unicode_escape"))
            # nhentai.xxx no emite `{"1": "j,1280,1850", ...}` como los demas, sino
            # `{"fl": {...paginas...}, "th": {...miniaturas...}, "ct": {...portada...}}`.
            # Iterando el nivel 1 salian 3 "paginas" llamadas fl/th/ct. Se baja al
            # sub-diccionario de las imagenes completas cuando el payload viene anidado.
            if payload and all (isinstance (valor ,dict )for valor in payload .values ()):
                payload =payload .get ("fl")or max (payload .values (),key =len )
            load_dir =inputs .get ("load_dir","").strip ("/")
            load_id =inputs .get ("load_id","")
            server_number =inputs .get ("load_server","")
            # El host del CDN se deduce de la portada. NO se busca por clase: en
            # hentaifox el <img> de la ficha no lleva ninguna (`<img src=".../cover.jpg">`),
            # asi que el filtro `cover|img-responsive` no encontraba nada, se caia al
            # host del sitio y las 137 paginas apuntaban a `hentaifox.com/...` -> 404.
            # Se busca por el nombre del archivo, que si es constante en los 8 sitios.
            cover =_first (
            root ,
            lambda node :node .tag =="img"
            and urlparse (_image_url (node ,chapter_id )).path .rsplit ("/",1 )[-1 ].startswith ("cover."),
            )
            host_portada =urlparse (_image_url (cover ,chapter_id )).hostname if cover else None 
            # La portada MANDA sobre `load_server`. Ese input solo dice el numero de
            # servidor, y componerlo como `m{n}.{dominio del sitio}` presupone que el
            # CDN vive bajo el mismo dominio: cierto en imhentai/hentaiera, falso en
            # nhentai.xxx, cuyo CDN es `i2.nhentaimg.com`. Ahi se generaban 199 URLs
            # apuntando a `m2.nhentai.xxx`, un host que ni siquiera resuelve.
            server =host_portada or (
            f"m{server_number }.{urlparse (self .base_url ).hostname }"
            if server_number 
            else urlparse (self .base_url ).hostname 
            )
            for key ,value in payload .items ():
                code =str (value ).split (",",1 )[0 ].strip ('"')
                extension ={"p":"png","b":"bmp","g":"gif","w":"webp"}.get (code ,"jpg")
                urls .append (f"https://{server }/{load_dir }/{load_id }/{key }.{extension }")
        else :
        # Sin payload JSON (asmhentai) las paginas se derivan de las miniaturas
        # `1t.jpg` -> `1.jpg`. El <img> NO es hijo directo del `.preview_thumb`:
        # va dentro de un `<a>`, asi que mirar solo `node.parent` no encontraba
        # ninguna y la galeria salia con 0 paginas. Se sube por los ancestros.
            for node in root .descendants ("img"):
                ancestro =node .parent 
                saltos =0 
                contenedor =False 
                while ancestro is not None and saltos <3 :
                    if any (
                    name in ancestro .attrs .get ("class","").split ()
                    for name in ("gallery_thumb","preview_thumb")
                    ):
                        contenedor =True 
                        break 
                    ancestro =ancestro .parent 
                    saltos +=1 
                if not contenedor :
                    continue 
                url =_image_url (node ,chapter_id )
                extension =url .rsplit (".",1 )[-1 ]
                urls .append (url .replace (f"t.{extension }",f".{extension }"))
                # El previsualizador solo pinta las 10 primeras y deja el resto tras un
                # boton "View More". El total real esta en `<input id="t_pages">`, y las
                # URLs son correlativas, asi que se completan sin pedir el ajax: sin esto
                # una galeria de 203 paginas se leia como si tuviera 10.
            total =inputs .get ("t_pages","").strip ()
            if urls and total .isdigit ()and len (urls )<int (total ):
                base_url ,_ ,ultimo =urls [0 ].rpartition ("/")
                numero ,punto ,extension =ultimo .rpartition (".")
                if numero .isdigit ()and punto :
                    urls =[
                    f"{base_url }/{indice }{punto }{extension }"
                    for indice in range (1 ,int (total )+1 )
                    ]
        return [
        SourcePage (
        source_id =url ,
        chapter_id =chapter_id ,
        index =index ,
        filename =url .rsplit ("/",1 )[-1 ].split ("?",1 )[0 ],
        source_name =self .name ,
        )
        for index ,url in enumerate (urls ,1 )
        ]


class GeneratedGalleryAdultsSource (GalleryAdultsSource ):

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
        "name":"Popular",
        "value":"pp"
        },
        {
        "name":"Latest",
        "value":"lt"
        },
        {
        "name":"Downloads",
        "value":"dl"
        },
        {
        "name":"Top Rated",
        "value":"tr"
        }
        ],
        "default":"pp"
        }
        ]
        return [SourceFilter (**item )for item in data ]

    name ='hentaizap_en'
    display_name ='HentaiZap (en)'
    base_url ='https://hentaizap.com'
    language ='en'
    manga_language ='english'
    profile ='hentaizap'


SOURCE =GeneratedGalleryAdultsSource

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
