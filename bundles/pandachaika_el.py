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


"""Adaptador de PandaChaika: el lector lee un ZIP remoto por rangos HTTP."""

_PANDA_ZIP ="nyanko-zip:"
_PANDA_DIGITS =re .compile (r"\d+")
_PANDA_LANGS ={
"en":"english","zh":"chinese","ko":"korean","es":"spanish","ru":"russian",
"pt":"portuguese","fr":"french","th":"thai","vi":"vietnamese","ja":"japanese",
"id":"indonesian","ar":"arabic","uk":"ukrainian","tr":"turkish","cs":"czech",
"tl":"tagalog","fi":"finnish","jv":"javanese","el":"greek",
}
_PANDA_TYPES =(
"All","Doujinshi","Manga","Image Set","Artist CG","Game CG","Western","Non-H","Misc",
)
_PANDA_SORTS =(
("public_date","Public Date"),("posted","Posted Date"),("title","Title"),
("title_jpn","Japanese Title"),("rating","Rating"),("filecount","Images"),
("filesize","File Size"),("category","Category"),
)
# (id, etiqueta, tipo que el Kotlin antepone a cada etiqueta)
_PANDA_TEXT =(
("tags","Tags",""),("male_tags","Male Tags","male"),("female_tags","Female Tags","female"),
("artists","Artists","artist"),("parodies","Parodies","parody"),
("characters","Characters","character"),
)
_PANDA_WEEKDAYS =("Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday")
_PANDA_MONTHS =(
"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec",
)


def _panda_titlecase (value :str )->str :
    return " ".join (part [:1 ].upper ()+part [1 :]if part else part for part in value .split (" "))


def _panda_filter_tags (tags :list [str ],include :str ="",exclude :tuple [str ,...]=())->str :
    chosen =[
    tag 
    for tag in tags 
    if tag .startswith (f"{include }:")
    and not any (tag .startswith (f"{name }:")for name in exclude )
    ]
    joined =", ".join (
    _panda_titlecase (tag .partition (":")[2 ].replace ("_"," "))for tag in chosen 
    )
    return joined 


def _panda_readable_size (value :float )->str :
    if value >=300 *1000 *1000 :
        return f"{value /1000.0 **3 :.2f} GB"
    if value >=100 *1000 :
        return f"{value /1000.0 **2 :.2f} MB"
    if value >=1000 :
        return f"{value /1000.0 :.2f} kB"
    return f"{value } B"


class PandaChaikaSource (MadaraSource ):
    """Cada archivo es una serie de un solo capitulo servido como ZIP."""

    @property 
    def search_language (self )->str :
        return _PANDA_LANGS .get (self .language ,"")

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("sort","Sort by","select",list (_PANDA_SORTS ),"public_date"),
        SourceFilter ("asc_desc","Dirección","select",[
        ("desc","Descendente"),("asc","Ascendente"),
        ],"desc"),
        SourceFilter ("category","Types","select",[
        (""if value =="All"else value ,value )for value in _PANDA_TYPES 
        ],""),
        *[
        SourceFilter (identifier ,label ,"text",default ="")
        for identifier ,label ,_ in _PANDA_TEXT 
        ],
        SourceFilter ("reason","Reason","text",default =""),
        SourceFilter ("uploader","Uploader","text",default =""),
        SourceFilter ("pages","Pages","text",default =""),
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        payload =await self ._search ([
        ("tags",self .search_language ),
        ("sort","rating"if kind =="popular"else "public_date"),
        ("apply",""),("json",""),("page",str (page )),
        ])
        return self ._results (payload )

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        query =query .strip ()
        if query .startswith ("https://"):
            if urlparse (query ).netloc !=urlparse (self .base_url ).netloc :
                raise ValueError ("URL no compatible")
                # El Kotlin arma aqui un id con dos tramos que luego no parsea; se
                # toma el identificador numerico, que es la intencion evidente.
            found =_PANDA_DIGITS .search (urlparse (query ).path )
            if found is None :
                raise ValueError ("URL no compatible")
            return await self ._by_id (int (found .group ()))
        if query .startswith ("id:"):
            return await self ._by_id (int (query [3 :].strip (" /")))
        for prefix ,transform in (
        ("ehentai:",lambda value :"https://e-hentai.org/g/"+re .sub (
        r"(?:https?://)?e-hentai\.org/g/","",value )),
        ("fakku:",lambda value :"https://www.fakku.net/hentai/"+re .sub (
        r"(?:https?://)?(?:www\.)?fakku\.net/hentai/","",value )),
        ("source:",lambda value :value ),
        ):
            if query .startswith (prefix ):
                payload =await self ._search ([
                ("qsearch",transform (query [len (prefix ):].strip ())),("json",""),
                ])
                archives =payload .get ("archives")or []
                if not archives :
                    raise SourceNotFoundError (f"{self .display_name }: no encontrado")
                return {"items":[self ._archive (archives [0 ])],"has_more":False }
        values =filters or {}
        tags =[self .search_language ]
        reason =""
        for identifier ,_ ,kind in _PANDA_TEXT :
            for part in str (values .get (identifier )or "").split (","):
                trimmed =part .strip ()
                if not trimmed :
                    continue 
                tags .append (
                ("-"if trimmed .startswith ("-")else "")
                +kind 
                +(":"if kind else "")
                +trimmed .casefold ().lstrip ("-")
                )
                # Ojo: el filtro "Uploader" del Kotlin declara el tipo "reason", asi que
                # pisa al anterior y el parametro uploader siempre viaja vacio.
        for identifier in ("reason","uploader"):
            if str (values .get (identifier )or ""):
                reason =str (values [identifier ])
        minimum ,maximum =self ._page_range (str (values .get ("pages")or ""))
        payload =await self ._search ([
        ("sort",str (values .get ("sort")or "public_date")),
        ("asc_desc","asc"if str (values .get ("asc_desc"))=="asc"else "desc"),
        ("category",str (values .get ("category")or "")),
        ("title",query ),
        ("tags",", ".join (tags )),
        ("filecount_from",str (minimum )),
        ("filecount_to",str (maximum )),
        ("reason",reason ),
        ("uploader",""),
        ("page",str (page )),
        ("apply",""),
        ("json",""),
        ])
        return self ._results (payload )

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        if isinstance (series ,SourceSeries ):
            return series 
        result =await self ._by_id (int (str (series )))
        if not result ["items"]:
            raise SourceNotFoundError (f"{self .display_name }: no encontrado")
        return result ["items"][0 ]

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        archive =await self ._api (series_id )
        download =str (archive .get ("download")or "")
        return [
        SourceChapter (
        source_id =download .partition ("/download/")[0 ].lstrip ("/"),
        title ="Chapter",
        series_id =series_id ,
        source_name =self .name ,
        number =1.0 ,
        language =self .language ,
        uploaded_at =self ._epoch (archive .get ("posted")),
        )
        ]

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        url =urljoin (f"{self .base_url }/",f"{chapter_id .strip ('/')}/download/")
        entries =await self ._zip_directory (url )
        entries .sort (key =lambda entry :entry ["name"].casefold ())
        return [
        SourcePage (
        source_id =_PANDA_ZIP +urlencode ({
        "u":url ,"n":entry ["name"],"o":entry ["offset"],
        "c":entry ["size"],"m":entry ["method"],
        }),
        chapter_id =chapter_id ,
        index =index ,
        filename =entry ["name"],
        source_name =self .name ,
        )
        for index ,entry in enumerate (entries )
        ]

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        url =page .source_id if isinstance (page ,SourcePage )else str (page )
        if not url .startswith (_PANDA_ZIP ):
            return await super ().page_bytes (page )
        values ={key :value [0 ]for key ,value in parse_qs (url [len (_PANDA_ZIP ):]).items ()}
        data =await self ._zip_entry (
        values ["u"],int (values ["o"]),int (values ["c"]),int (values ["m"]),
        )
        suffix =values ["n"].rpartition (".")[2 ].casefold ()
        return SourcePageContent (
        media_type =f"image/{'jpeg'if suffix =='jpg'else suffix or 'jpeg'}",
        chunks =iter ([data ]),
        )

        # -------------------------------------------------------------- internals
    async def _search (self ,params :list [tuple [str ,str ]])->dict :
        response =await self ._request ("GET",f"{self .base_url }/search/",params =params )
        response .raise_for_status ()
        return response .json ()or {}

    async def _api (self ,archive_id :str )->dict :
        response =await self ._request (
        "GET",f"{self .base_url }/api",params ={"archive":archive_id },
        )
        response .raise_for_status ()
        return response .json ()or {}

    async def _by_id (self ,archive_id :int )->dict :
        archive =await self ._api (str (archive_id ))
        payload =await self ._search ([("qsearch",str (archive .get ("title")or "")),("json","")])
        for item in payload .get ("archives")or []:
            if isinstance (item ,dict )and item .get ("id")==archive_id :
                return {"items":[self ._archive (item )],"has_more":False }
        raise SourceNotFoundError (f"{self .display_name }: identificador invalido")

    def _results (self ,payload :dict )->dict :
        return {
        "items":[
        self ._archive (item )
        for item in payload .get ("archives")or []
        if isinstance (item ,dict )
        ],
        "has_more":bool (payload .get ("has_next")),
        }

    def _archive (self ,item :dict )->SourceSeries :
        tags =[str (tag )for tag in item .get ("tags")or []]
        groups =_panda_filter_tags (tags ,"group")
        artists =_panda_filter_tags (tags ,"artist")
        publishers =_panda_filter_tags (tags ,"publisher")
        characters =_panda_filter_tags (tags ,"character")
        male =_panda_filter_tags (tags ,"male")
        female =_panda_filter_tags (tags ,"female")
        others =_panda_filter_tags (
        tags ,exclude =("female","male","artist","publisher","group","parody"),
        )
        parodies =_panda_filter_tags (tags ,"parody")
        parts =[f"Uploader: {item .get ('uploader')or 'Anonymous'}\n"]
        if publishers :
            parts .append (f"Publishers: {publishers }\n")
        parts .append ("\n")
        if parodies :
            parts .append (f"Parodies: {parodies }\n")
        if characters :
            parts .append (f"Characters: {characters }\n")
        if parodies or characters :
            parts .append ("\n")
        for label ,value in (("Male tags",male ),("Female tags",female ),("Other tags",others )):
            if value :
                parts .append (f"{label }: {value }\n\n")
        if item .get ("title_jpn"):
            parts .append (f"Japanese Title: {item ['title_jpn']}\n")
        parts .append (f"Pages: {item .get ('filecount')}\n")
        parts .append (f"File Size: {_panda_readable_size (float (item .get ('filesize')or 0 ))}\n")
        for label ,key in (("Public Date","public_date"),("Posted","posted")):
            stamp =self ._readable_date (item .get (key ))
            if stamp :
                parts .append (f"{label }: {stamp }\n")
        return SourceSeries (
        source_id =str (item .get ("id")),
        title =str (item .get ("title")or ""),
        source_name =self .name ,
        cover_url =str (item .get ("thumbnail"))if item .get ("thumbnail")else None ,
        description ="".join (parts ),
        author =groups or artists or None ,
        artist =artists or None ,
        status ="completed",
        content_tags =tuple (
        value for value in ", ".join (filter (None ,(male ,female ,others ))).split (", ")if value 
        ),
        web_url =f"{self .base_url }/archive/{item .get ('id')}",
        )

    async def _zip_directory (self ,url :str )->list [dict ]:
        import struct 

        tail =await self ._range (url ,"bytes=-65536")
        marker =tail .rfind (b"PK\x05\x06")
        if marker <0 :
            raise SourceNotFoundError (f"{self .display_name }: ZIP sin directorio")
        size ,offset =struct .unpack ("<II",tail [marker +12 :marker +20 ])
        locator =tail .rfind (b"PK\x06\x07")
        if locator >=0 and offset ==0xFFFFFFFF :
        # ZIP64: el directorio vive mas alla de los 4 GB direccionables.
            end =struct .unpack ("<Q",tail [locator +8 :locator +16 ])[0 ]
            header =await self ._range (url ,f"bytes={end }-{end +55 }")
            size ,offset =struct .unpack ("<QQ",header [40 :56 ])
        directory =await self ._range (url ,f"bytes={offset }-{offset +size -1 }")
        entries :list [dict ]=[]
        cursor =0 
        while cursor +46 <=len (directory )and directory [cursor :cursor +4 ]==b"PK\x01\x02":
            method =struct .unpack ("<H",directory [cursor +10 :cursor +12 ])[0 ]
            compressed =struct .unpack ("<I",directory [cursor +20 :cursor +24 ])[0 ]
            name_length ,extra_length ,comment_length =struct .unpack (
            "<HHH",directory [cursor +28 :cursor +34 ],
            )
            local =struct .unpack ("<I",directory [cursor +42 :cursor +46 ])[0 ]
            name =directory [cursor +46 :cursor +46 +name_length ].decode ("utf-8","replace")
            extra =directory [cursor +46 +name_length :cursor +46 +name_length +extra_length ]
            compressed ,local =self ._zip64 (extra ,compressed ,local )
            if not name .endswith ("/"):
                entries .append (
                {"name":name ,"offset":local ,"size":compressed ,"method":method },
                )
            cursor +=46 +name_length +extra_length +comment_length 
        return entries 

    async def _zip_entry (self ,url :str ,offset :int ,size :int ,method :int )->bytes :
        import struct 
        import zlib 

        slack =4096 
        block =await self ._range (url ,f"bytes={offset }-{offset +30 +slack +size -1 }")
        name_length ,extra_length =struct .unpack ("<HH",block [26 :30 ])
        start =30 +name_length +extra_length 
        if start +size >len (block ):
            block =await self ._range (
            url ,f"bytes={offset +start }-{offset +start +size -1 }",
            )
            start =0 
        data =block [start :start +size ]
        return zlib .decompress (data ,-15 )if method ==8 else data 

    async def _range (self ,url :str ,value :str )->bytes :
        response =await self ._request ("GET",url ,headers ={"Range":value })
        response .raise_for_status ()
        return response .content 

    @staticmethod 
    def _zip64 (extra :bytes ,compressed :int ,local :int )->tuple [int ,int ]:
        import struct 

        if compressed !=0xFFFFFFFF and local !=0xFFFFFFFF :
            return compressed ,local 
        cursor =0 
        while cursor +4 <=len (extra ):
            tag ,length =struct .unpack ("<HH",extra [cursor :cursor +4 ])
            body =extra [cursor +4 :cursor +4 +length ]
            if tag ==0x0001 :
                values =list (struct .unpack (f"<{len (body )//8 }Q",body [:len (body )//8 *8 ]))
                index =0 
                if compressed ==0xFFFFFFFF and index <len (values ):
                # El orden ZIP64 es sin comprimir, comprimido, offset local.
                    compressed =values [min (1 ,len (values )-1 )]
                if local ==0xFFFFFFFF and values :
                    local =values [-1 ]
            cursor +=4 +length 
        return compressed ,local 

    @staticmethod 
    def _page_range (query :str ,minimum :int =1 ,maximum :int =9999 )->tuple [int ,int ]:
        digits ="".join (character for character in query if character .isdigit ())
        number =int (digits )if digits else -1 

        def limited (value :int =number )->int :
            return max (minimum ,min (maximum ,value ))

        if number <0 :
            return minimum ,maximum 
        first =query [0 ]if query else ""
        second =query [1 ]if len (query )>1 else ""
        if first =="<":
            return 1 ,limited ()if second =="="else limited (number +1 )
        if first ==">":
            return (limited (number )if second =="="else limited (number +1 )),maximum 
        if first =="=":
            if second ==">":
                return limited (),maximum 
            if second =="<":
                return 1 ,limited (maximum )
            return limited (),limited ()
        return limited (),limited ()

    @staticmethod 
    def _epoch (value :Any )->str |None :
        from datetime import datetime ,timezone 

        try :
            moment =datetime .fromtimestamp (int (value ),timezone .utc )
        except (TypeError ,ValueError ,OSError ,OverflowError ):
            return None 
        return moment .replace (tzinfo =None ).isoformat ()

    @staticmethod 
    def _readable_date (value :Any )->str |None :
        from datetime import datetime ,timezone 

        try :
            moment =datetime .fromtimestamp (int (value ),timezone .utc )
        except (TypeError ,ValueError ,OSError ,OverflowError ):
            return None 
        return (
        f"{_PANDA_WEEKDAYS [moment .weekday ()]}, {moment .day } "
        f"{_PANDA_MONTHS [moment .month -1 ]} {moment .year } {moment :%H:%M} (UTC)"
        )


class GeneratedPandaChaikaSource (PandaChaikaSource ):
    name ='pandachaika_el'
    display_name ='PandaChaika'
    base_url ='https://panda.chaika.moe'
    language ='el'
    requests_per_minute =60 
    content_warning ='nsfw'
    image_headers ={'Referer':'https://panda.chaika.moe/'}


SOURCE =GeneratedPandaChaikaSource

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
