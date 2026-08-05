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


"""Adaptador de xkcd: cinco traducciones con archivos y lectores distintos."""

_XKCD_ENGLISH ="https://xkcd.com"
_XKCD_TEXT ="nyanko-text:"
_XKCD_CACHE_SECONDS =60 *60 
_XKCD_PER_PAGE =10 
_XKCD_SPANISH_OVERRIDES ={"/strips/geografia/":1472 }
_XKCD_ARCHIVE ={"fr":"/tous-episodes.php","ru":"/img","zh":"/api/strips.json"}
_XKCD_CREATOR ={"ru":"Рэндел Манро","zh":"兰德尔·门罗"}
_XKCD_SYNOPSIS ={
"es":"Un webcómic sobre romance, sarcasmo, mates y lenguaje.",
"fr":"Un webcomic sarcastique qui parle de romance, de maths et de langage.",
"ru":"о романтике, сарказме, математике и языке",
"zh":"這裡翻譯某個關於浪漫、諷刺、數學、以及語言的漫畫",
}
_XKCD_INTERACTIVE ={
"es":"Para experimentar la versión interactiva de este cómic, ábralo en WebView/navegador.",
"zh":"要體驗本漫畫的互動版請在WebView/瀏覽器中打開。",
}


def _xkcd_children (node :_Node ,tag :str |None =None )->list [_Node ]:
    return [
    child 
    for child in node .children 
    if isinstance (child ,_Node )and (tag is None or child .tag ==tag )
    ]


def _xkcd_is_last_element (node :_Node )->bool :
    parent =node .parent 
    if parent is None :
        return True 
    elements =_xkcd_children (parent )
    return bool (elements )and elements [-1 ]is node 


def _xkcd_by_id (root :_Node ,identifier :str )->_Node |None :
    return _first (root ,lambda node :node .attrs .get ("id")==identifier )


class XkcdSource (MadaraSource ):
    """El numero de tira es comun a todos los idiomas; las fechas salen del archivo ingles."""

    supports_latest =False 

    def __init__ (self ,fetcher :SourceFetcher |None =None )->None :
        super ().__init__ (fetcher )
        self ._dates :dict [int ,str ]|None =None 
        self ._dates_at =0.0 
        self ._chapters :list [SourceChapter ]|None =None 
        self ._chapters_at =0.0 

        # ---------------------------------------------------------------- config
    @property 
    def archive_path (self )->str :
        return _XKCD_ARCHIVE .get (self .language ,"/archive")

    @property 
    def creator (self )->str :
        return _XKCD_CREATOR .get (self .language ,"Randall Munroe")

    @property 
    def synopsis (self )->str :
        return _XKCD_SYNOPSIS .get (self .language ,"A webcomic of romance, sarcasm, math and language.")

    @property 
    def interactive_text (self )->str :
        return _XKCD_INTERACTIVE .get (
        self .language ,"To experience the interactive version of this comic, open it in WebView/browser.",
        )

    def get_filters (self )->list [SourceFilter ]:
        return []

    def get_preferences (self )->list [SourcePreference ]:
        return [
        SourcePreference (
        id ="organization_method",
        name ="Organization Method",
        type ="select",
        options =[
        ("SINGLE","Single manga (all comics)"),
        ("BY_YEAR","By year"),
        ("BY_YEAR_MONTH","By year-month"),
        ],
        default ="SINGLE",
        )
        ]

        # --------------------------------------------------------------- catalog
    async def browse (self ,kind :str ,page :int =1 ):
        if kind !="popular":
            return {"items":[],"has_more":False }
        groups =await self ._grouped ()
        keys =sorted (groups ,reverse =True )
        start =(page -1 )*_XKCD_PER_PAGE 
        window =keys [start :start +_XKCD_PER_PAGE ]
        items :list [SourceSeries ]=[]
        for key in window :
            first =groups [key ][0 ]if groups [key ]else None 
            items .append (
            SourceSeries (
            source_id =key ,
            title ="xkcd"if key =="SINGLE"else f"xkcd {key }",
            source_name =self .name ,
            cover_url =await self ._thumbnail (first )if first is not None else None ,
            description =self .synopsis ,
            author =self .creator ,
            artist =self .creator ,
            status ="ongoing",
            web_url =self .base_url ,
            )
            )
        return {"items":items ,"has_more":start +_XKCD_PER_PAGE <len (keys )}

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
    # El Kotlin no implementa busqueda: siempre devuelve vacio.
        return {"items":[],"has_more":False }

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        if isinstance (series ,SourceSeries ):
            return series 
        return SourceSeries (
        source_id =str (series ),
        title ="xkcd"if str (series )=="SINGLE"else f"xkcd {series }",
        source_name =self .name ,
        description =self .synopsis ,
        author =self .creator ,
        artist =self .creator ,
        status ="ongoing",
        web_url =self .base_url ,
        )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        key =series .source_id if isinstance (series ,SourceSeries )else str (series )
        return (await self ._grouped ()).get (key ,[])

        # ----------------------------------------------------------------- pages
    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        response =await self ._request ("GET",urljoin (f"{self .base_url }/",chapter_id .lstrip ("/")))
        response .raise_for_status ()
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        container =self ._container (root )
        if container is None :
            raise ValueError (self .interactive_text )
        image =self ._image_node (container )
        if image is None :
            raise ValueError (self .interactive_text )
        source =self ._image_url (image ,base )
        first ,second =self ._texts (root ,image )
        return [
        SourcePage (
        source_id =source ,
        chapter_id =chapter_id ,
        index =0 ,
        filename =urlparse (source ).path .rsplit ("/",1 )[-1 ]or "0.png",
        source_name =self .name ,
        ),
        SourcePage (
        source_id =_XKCD_TEXT +urlencode ({"alt":first ,"title":second }),
        chapter_id =chapter_id ,
        index =1 ,
        filename ="1.png",
        source_name =self .name ,
        ),
        ]

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        url =page .source_id if isinstance (page ,SourcePage )else str (page )
        if not url .startswith (_XKCD_TEXT ):
            return await super ().page_bytes (page )
            # El Kotlin delega en TextInterceptor; aqui la tira de texto se dibuja.
        values =parse_qs (url [len (_XKCD_TEXT ):])
        rendered =self ._render (
        values .get ("alt",[""])[0 ],values .get ("title",[""])[0 ],
        )
        return SourcePageContent (media_type ="image/png",chunks =iter ([rendered ]))

        # -------------------------------------------------------------- internals
    async def _grouped (self )->dict [str ,list [SourceChapter ]]:
    # Solo el modo SINGLE es alcanzable: la app no devuelve el valor elegido.
        return {"SINGLE":await self ._all_chapters ()}

    async def _all_chapters (self )->list [SourceChapter ]:
        import time 

        now =time .time ()
        if self ._chapters is None or now -self ._chapters_at >_XKCD_CACHE_SECONDS :
            response =await self ._request ("GET",f"{self .base_url }{self .archive_path }")
            response .raise_for_status ()
            self ._chapters =await self ._parse_archive (response )
            self ._chapters_at =now 
        return self ._chapters 

    async def _english_dates (self )->dict [int ,str ]:
        import time 

        now =time .time ()
        if self ._dates is None or now -self ._dates_at >_XKCD_CACHE_SECONDS :
            try :
                response =await self ._request ("GET",f"{_XKCD_ENGLISH }/archive/")
                response .raise_for_status ()
                root =_parse_html (response .text )
                holder =_xkcd_by_id (root ,"middleContainer")
                self ._dates ={
                number :anchor .attrs .get ("title","")
                for anchor in (_xkcd_children (holder ,"a")if holder is not None else [])
                if (number :=self ._number (anchor .attrs .get ("href","")))is not None 
                }
            except Exception :
                self ._dates ={}
            self ._dates_at =now 
        return self ._dates 

    async def _parse_archive (self ,response :Any )->list [SourceChapter ]:
        dates =await self ._english_dates ()
        if self .language =="zh":
            payload =response .json ()or {}
            return [
            self ._chapter (
            f"/{item ['id']}",int (item ["id"]),str (item .get ("title")or ""),
            dates .get (int (item ["id"])),
            )
            for item in payload .values ()
            if isinstance (item ,dict )and item .get ("id")is not None 
            ]
        root =_parse_html (response .text )
        base =str (response .url )or self .base_url 
        anchors =self ._archive_anchors (root )
        result :list [SourceChapter ]=[]
        if self .language =="es":
            by_date ={self ._normalize (value ):number for number ,value in dates .items ()}
            for anchor in anchors :
                parent =anchor .parent 
                moment =_first (parent ,lambda node :node .tag =="time")if parent is not None else None 
                if moment is None :
                    continue 
                stamp =moment .text ().strip ()
                path =urlparse (urljoin (base ,anchor .attrs .get ("href",""))).path 
                number =_XKCD_SPANISH_OVERRIDES .get (path )or by_date .get (self ._normalize (stamp ))
                if number is None :
                    continue 
                result .append (self ._chapter (path ,number ,anchor .text ().strip (),stamp ))
            return result 
        for anchor in anchors :
            parsed =urlparse (urljoin (base ,anchor .attrs .get ("href","")))
            path =parsed .path +(f"?{parsed .query }"if parsed .query else "")
            if self .language =="fr":
            # La tira va en la query: /tous-episodes.php?num=123
                number =self ._int (path .rpartition ("=")[2 ])
                title =anchor .text ().strip ()
            elif self .language =="ru":
                number =self ._number (parsed .path )
                children =_xkcd_children (anchor )
                title =children [0 ].attrs .get ("alt","")if children else ""
            else :
                number =self ._number (parsed .path )
                title =anchor .text ().strip ()
            result .append (
            self ._chapter (
            path ,number or 0 ,title ,
            dates .get (number )if number is not None else None ,
            )
            )
        return result [::-1 ]if self .language =="fr"else result 

    def _archive_anchors (self ,root :_Node )->list [_Node ]:
        if self .language =="es":
            return [
            anchor 
            for holder in root .descendants ()
            if holder .has_class ("archive-entry")
            for anchor in _xkcd_children (holder ,"a")
            ]
        if self .language =="fr":
            content =_xkcd_by_id (root ,"content")
            anchors :list [_Node ]=[]
            for holder in content .descendants ()if content is not None else []:
                if not holder .has_class ("s"):
                    continue 
                found =_xkcd_children (holder ,"a")
                # ":not(:last-of-type)" descarta el ultimo enlace del bloque.
                anchors .extend (found [:-1 ]if found else [])
            return anchors 
        if self .language =="ru":
            return [
            anchor 
            for holder in root .descendants ()
            if holder .has_class ("main")
            for anchor in _xkcd_children (holder ,"a")
            ]
        holder =_xkcd_by_id (root ,"middleContainer")
        return _xkcd_children (holder ,"a")if holder is not None else []

    def _container (self ,root :_Node )->_Node |None :
        if self .language =="es":
            content =_xkcd_by_id (root ,"middleContent")
            return next (
            (node for node in content .descendants ()if node .has_class ("strip")),
            None ,
            )if content is not None else None 
        if self .language =="fr":
            content =_xkcd_by_id (root ,"content")
            return next (
            (node for node in content .descendants ()if node .has_class ("s")),None ,
            )if content is not None else None 
        if self .language =="ru":
            return next ((node for node in root .descendants ()if node .has_class ("main")),None )
        if self .language =="zh":
            content =_xkcd_by_id (root ,"content")
            return next (
            (node for node in _xkcd_children (content ,"img")if not node .attrs .get ("id")),None ,
            )if content is not None else None 
        comic =_xkcd_by_id (root ,"comic")
        return next (iter (_xkcd_children (comic ,"img")),None )if comic is not None else None 

    def _image_node (self ,container :_Node )->_Node |None :
        if self .language =="fr":
            return _first (
            container ,
            lambda node :node .tag =="img"and node .attrs .get ("src","").startswith ("strips/"),
            )
        if self .language =="ru":
            return _first (
            container ,lambda node :node .tag =="img"and "/i/"in node .attrs .get ("src",""),
            )
        if self .language =="zh":
            return container 
        return container if _xkcd_is_last_element (container )else None 

    def _image_url (self ,image :_Node ,base :str )->str :
        if self .language in {"fr","ru","zh"}or not image .attrs .get ("srcset"):
            return urljoin (base ,image .attrs .get ("src",""))
        return urljoin (base ,image .attrs ["srcset"].split (" ",1 )[0 ])

    def _texts (self ,root :_Node ,image :_Node )->tuple [str ,str ]:
        first =image .attrs .get ("alt","")
        second =image .attrs .get ("title","")
        if self .language =="fr":
            content =_xkcd_by_id (root ,"content")
            block =next (
            (
            node 
            for holder in (content .descendants ()if content is not None else [])
            if holder .has_class ("s")
            for node in holder .descendants ("div")
            if not node .has_class ("buttons")
            ),
            None ,
            )
            first =(block .text ().strip ()if block is not None else "")or first 
        if self .language =="ru":
            block =next (
            (node for node in root .descendants ()if node .has_class ("comics_text")),None ,
            )
            second =(block .text ().strip ()if block is not None else "")or first 
        return first ,second 

    async def _thumbnail (self ,chapter :SourceChapter )->str |None :
        try :
            response =await self ._request (
            "GET",urljoin (f"{self .base_url }/",chapter .source_id .lstrip ("/")),
            )
            response .raise_for_status ()
            root =_parse_html (response .text )
            base =str (response .url )or self .base_url 
            container =self ._container (root )
            image =None 
            if container is not None :
                image =(
                self ._image_node (container )
                if self .language in {"fr","ru"}
                else container 
                )
            image =image or _first (root ,lambda node :node .tag =="img"and node .attrs .get ("alt"))
            if image is None :
                return None 
            value =self ._image_url (image ,base )
            return value if value and "thumbnail"not in value else None 
        except Exception :
            return None 

    def _chapter (self ,path :str ,number :int ,title :str ,stamp :str |None )->SourceChapter :
        return SourceChapter (
        source_id =path .lstrip ("/"),
        title =f"{number }: {title }",
        series_id ="SINGLE",
        source_name =self .name ,
        number =float (number ),
        language =self .language ,
        uploaded_at =self ._date (stamp ),
        )

    @staticmethod 
    def _render (alt :str ,title :str )->bytes :
        import textwrap 

        from PIL import ImageDraw ,ImageFont 

        font =ImageFont .load_default ()
        lines :list [str ]=[]
        for block in (alt ,title ):
            if not block :
                continue 
            if lines :
                lines .append ("")
            lines .extend (textwrap .wrap (block ,width =60 )or [""])
        lines =lines or [""]
        width ,height =640 ,24 +18 *len (lines )
        canvas =Image .new ("RGB",(width ,height ),"white")
        draw =ImageDraw .Draw (canvas )
        for index ,line in enumerate (lines ):
            draw .text ((12 ,12 +18 *index ),line ,fill ="black",font =font )
        buffer =io .BytesIO ()
        canvas .save (buffer ,"PNG")
        return buffer .getvalue ()

    @staticmethod 
    def _normalize (value :str )->str :
        parts =value .strip ().split ("-")
        if len (parts )!=3 :
            return value .strip ()
        return f"{parts [0 ]}-{parts [1 ].zfill (2 )}-{parts [2 ].zfill (2 )}"

    @classmethod 
    def _number (cls ,value :str )->int |None :
        return cls ._int (value .strip ("/"))

    @staticmethod 
    def _int (value :str )->int |None :
        try :
            return int (value )
        except (TypeError ,ValueError ):
            return None 

    @classmethod 
    def _date (cls ,value :str |None )->str |None :
        from datetime import datetime 

        if not value :
            return None 
        try :
            return datetime .strptime (cls ._normalize (value ),"%Y-%m-%d").isoformat ()
        except ValueError :
            return None 


class GeneratedXkcdSource (XkcdSource ):
    name ='xkcd_es'
    display_name ='xkcd'
    base_url ='https://es.xkcd.com'
    language ='es'
    requests_per_minute =60 
    content_warning ='safe'
    image_headers ={'Referer':'https://es.xkcd.com/'}


SOURCE =GeneratedXkcdSource

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
