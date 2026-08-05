"""Implementación común del tema Madara para bundles Nyanko Source v3."""

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

"""Fuente HTTP adaptable para extensiones sin un motor compartido."""

import json 
import re 
from urllib .parse import urljoin 



class GenericSource (MadaraSource ):
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
        "name":"16+",
        "value":"16"
        },
        {
        "name":"18+",
        "value":"18"
        },
        {
        "name":"19+",
        "value":"19"
        },
        {
        "name":"1X1",
        "value":"1x1"
        },
        {
        "name":"21+",
        "value":"21"
        },
        {
        "name":"3D Hentai",
        "value":"3d-hentai"
        },
        {
        "name":"3P",
        "value":"3p"
        },
        {
        "name":"Abo",
        "value":"abo"
        },
        {
        "name":"Action",
        "value":"action"
        },
        {
        "name":"Adult",
        "value":"adult"
        },
        {
        "name":"Adventure",
        "value":"adventure"
        },
        {
        "name":"Ahegao",
        "value":"ahegao"
        },
        {
        "name":"Ám Ảnh",
        "value":"am-anh"
        },
        {
        "name":"Anal",
        "value":"anal"
        },
        {
        "name":"Anh Chàng Mưu Mô/ Ranh Mãnh",
        "value":"anh-chang-muu-mo-ranh-manh"
        },
        {
        "name":"Anh Chàng Nhỏ Tuổi",
        "value":"anh-chang-nho-tuoi"
        },
        {
        "name":"Animal",
        "value":"animal"
        },
        {
        "name":"Animal Girl",
        "value":"animal-girl"
        },
        {
        "name":"Anime",
        "value":"anime"
        },
        {
        "name":"Ảo Tưởng",
        "value":"ao-tuong"
        },
        {
        "name":"Art Book",
        "value":"art-book"
        },
        {
        "name":"Artist",
        "value":"artist"
        },
        {
        "name":"Artist Cg",
        "value":"artist-cg"
        },
        {
        "name":"Âu Cổ",
        "value":"au-co"
        },
        {
        "name":"Bách Hợp",
        "value":"bach-hop"
        },
        {
        "name":"Bạn Bè Thành Người Yêu",
        "value":"ban-be-thanh-nguoi-yeu"
        },
        {
        "name":"Bạn Thời Thơ Ấu",
        "value":"ban-thoi-tho-au"
        },
        {
        "name":"Bạn Tình",
        "value":"ban-tinh"
        },
        {
        "name":"Bạo Dâm",
        "value":"bao-dam"
        },
        {
        "name":"Based Game",
        "value":"based-game"
        },
        {
        "name":"Bbm",
        "value":"bbm"
        },
        {
        "name":"Bbw",
        "value":"bbw"
        },
        {
        "name":"Bdsm",
        "value":"bdsm"
        },
        {
        "name":"Beach",
        "value":"beach"
        },
        {
        "name":"Beast",
        "value":"beast"
        },
        {
        "name":"Bestiality",
        "value":"bestiality"
        },
        {
        "name":"Bí Ẩn",
        "value":"bi-an"
        },
        {
        "name":"Bi Kịch",
        "value":"bi-kich"
        },
        {
        "name":"Bị Thuốc",
        "value":"bi-thuoc"
        },
        {
        "name":"Big Ass",
        "value":"big-ass"
        },
        {
        "name":"Big Boobs",
        "value":"big-boobs"
        },
        {
        "name":"Big Breasts",
        "value":"big-breasts"
        },
        {
        "name":"Big Dick",
        "value":"big-dick"
        },
        {
        "name":"Big Penis",
        "value":"big-penis"
        },
        {
        "name":"Big Vagina",
        "value":"big-vagina"
        },
        {
        "name":"Black Skin",
        "value":"black-skin"
        },
        {
        "name":"Blackmail",
        "value":"blackmail"
        },
        {
        "name":"Bloomers",
        "value":"bloomers"
        },
        {
        "name":"Blow Job",
        "value":"blow-job"
        },
        {
        "name":"Blowjob",
        "value":"blowjob"
        },
        {
        "name":"Blowjobs",
        "value":"blowjobs"
        },
        {
        "name":"Body Modification",
        "value":"body-modification"
        },
        {
        "name":"Body Modifications",
        "value":"body-modifications"
        },
        {
        "name":"Body Swap",
        "value":"body-swap"
        },
        {
        "name":"Body Writting",
        "value":"body-writting"
        },
        {
        "name":"Bodysuit",
        "value":"bodysuit"
        },
        {
        "name":"Bối Cảnh Phương Tây",
        "value":"boi-canh-phuong-tay"
        },
        {
        "name":"Bondage",
        "value":"bondage"
        },
        {
        "name":"Boy Love",
        "value":"boy-love"
        },
        {
        "name":"Boylove",
        "value":"boylove"
        },
        {
        "name":"Breast Sucking",
        "value":"breast-sucking"
        },
        {
        "name":"Breastjobs",
        "value":"breastjobs"
        },
        {
        "name":"Brocon",
        "value":"brocon"
        },
        {
        "name":"Brother",
        "value":"brother"
        },
        {
        "name":"Bukkake",
        "value":"bukkake"
        },
        {
        "name":"Business Suit",
        "value":"business-suit"
        },
        {
        "name":"Cặc Bự",
        "value":"cac-bu"
        },
        {
        "name":"Cáo Già X Thỏ Con",
        "value":"cao-gia-x-tho-con"
        },
        {
        "name":"Cặp Đôi Chênh Lệch Tuổi Tác",
        "value":"cap-doi-chenh-lech-tuoi-tac"
        },
        {
        "name":"Cặp Đôi Thanh Mai Trúc Mã",
        "value":"cap-doi-thanh-mai-truc-ma"
        },
        {
        "name":"Catgirls",
        "value":"catgirls"
        },
        {
        "name":"Cg",
        "value":"cg"
        },
        {
        "name":"Chàng Trai Cáu Kỉnh",
        "value":"chang-trai-cau-kinh"
        },
        {
        "name":"Chàng Trai Mưu Mô",
        "value":"chang-trai-muu-mo"
        },
        {
        "name":"Chàng Trai Yêu Đơn Phương",
        "value":"chang-trai-yeu-don-phuong"
        },
        {
        "name":"Che Ít",
        "value":"che-it"
        },
        {
        "name":"Che Nhiều",
        "value":"che-nhieu"
        },
        {
        "name":"Cheating",
        "value":"cheating"
        },
        {
        "name":"Chênh Lệch Thân Phận",
        "value":"chenh-lech-than-phan"
        },
        {
        "name":"Chị / Em",
        "value":"chi-em"
        },
        {
        "name":"Chị Gái Hơn Tuổi X Em Trai Kém Tuổi",
        "value":"chi-gai-hon-tuoi-x-em-trai-kem-tuoi"
        },
        {
        "name":"Chiếm Hữu",
        "value":"chiem-huu"
        },
        {
        "name":"Chikan",
        "value":"chikan"
        },
        {
        "name":"Chinese Dress",
        "value":"chinese-dress"
        },
        {
        "name":"Chơi Hai Lỗ",
        "value":"choi-hai-lo"
        },
        {
        "name":"Chữa Lành",
        "value":"chua-lanh"
        },
        {
        "name":"Chuyển Sinh",
        "value":"chuyen-sinh"
        },
        {
        "name":"Cô / Dì",
        "value":"co-di"
        },
        {
        "name":"Có Che",
        "value":"co-che"
        },
        {
        "name":"Cổ Đại",
        "value":"co-dai"
        },
        {
        "name":"Cô Gái Ngây Thơ",
        "value":"co-gai-ngay-tho"
        },
        {
        "name":"Cô Gái Từng Bị Tổn Thương",
        "value":"co-gai-tung-bi-ton-thuong"
        },
        {
        "name":"Cô Gái Yêu Đơn Phương",
        "value":"co-gai-yeu-don-phuong"
        },
        {
        "name":"Cô Nàng Thẳng Thắn",
        "value":"co-nang-thang-than"
        },
        {
        "name":"Cô Nàng Từng Bị Tổn Thương",
        "value":"co-nang-tung-bi-ton-thuong"
        },
        {
        "name":"Cổ Trang",
        "value":"co-trang"
        },
        {
        "name":"Color",
        "value":"color"
        },
        {
        "name":"Comedy",
        "value":"comedy"
        },
        {
        "name":"Comic",
        "value":"comic"
        },
        {
        "name":"Comic 18+",
        "value":"comic-18"
        },
        {
        "name":"Complete",
        "value":"complete"
        },
        {
        "name":"Con Gái",
        "value":"con-gai"
        },
        {
        "name":"Côn Trùng",
        "value":"con-trung"
        },
        {
        "name":"Condom",
        "value":"condom"
        },
        {
        "name":"Công Sở",
        "value":"cong-so"
        },
        {
        "name":"Cosplay",
        "value":"cosplay"
        },
        {
        "name":"Cốt Truyện",
        "value":"cot-truyen"
        },
        {
        "name":"Cousin",
        "value":"cousin"
        },
        {
        "name":"Creampie",
        "value":"creampie"
        },
        {
        "name":"Cunnilingus",
        "value":"cunnilingus"
        },
        {
        "name":"Cứu Rỗi",
        "value":"cuu-roi"
        },
        {
        "name":"Cứu Rỗi Lẫn Nhau",
        "value":"cuu-roi-lan-nhau"
        },
        {
        "name":"Đã Full",
        "value":"da-full"
        },
        {
        "name":"Đam Mỹ",
        "value":"dam-my"
        },
        {
        "name":"Dark Skin",
        "value":"dark-skin"
        },
        {
        "name":"Deepthroat",
        "value":"deepthroat"
        },
        {
        "name":"Defloration",
        "value":"defloration"
        },
        {
        "name":"Demon",
        "value":"demon"
        },
        {
        "name":"Demon Girl",
        "value":"demon-girl"
        },
        {
        "name":"Demongirl",
        "value":"demongirl"
        },
        {
        "name":"Đeo Kính",
        "value":"deo-kinh"
        },
        {
        "name":"Devil",
        "value":"devil"
        },
        {
        "name":"Devilgirl",
        "value":"devilgirl"
        },
        {
        "name":"Dickgirl",
        "value":"dickgirl"
        },
        {
        "name":"Dirty",
        "value":"dirty"
        },
        {
        "name":"Dirty Old Man",
        "value":"dirty-old-man"
        },
        {
        "name":"Dirty Talk",
        "value":"dirty-talk"
        },
        {
        "name":"Đồ Bơi",
        "value":"do-boi"
        },
        {
        "name":"Đồ Chơi Tình Dục",
        "value":"do-choi-tinh-duc"
        },
        {
        "name":"Đô Thị",
        "value":"do-thi"
        },
        {
        "name":"Double Penetration",
        "value":"double-penetration"
        },
        {
        "name":"Doujinshi",
        "value":"doujinshi"
        },
        {
        "name":"Drama",
        "value":"drama"
        },
        {
        "name":"Drug",
        "value":"drug"
        },
        {
        "name":"Ecchi",
        "value":"ecchi"
        },
        {
        "name":"Echi",
        "value":"echi"
        },
        {
        "name":"Elder Sister",
        "value":"elder-sister"
        },
        {
        "name":"Elf",
        "value":"elf"
        },
        {
        "name":"Exhibitionism",
        "value":"exhibitionism"
        },
        {
        "name":"Exhibitionist",
        "value":"exhibitionist"
        },
        {
        "name":"Family",
        "value":"family"
        },
        {
        "name":"Fantasy",
        "value":"fantasy"
        },
        {
        "name":"Father",
        "value":"father"
        },
        {
        "name":"Femdom",
        "value":"femdom"
        },
        {
        "name":"Fendom",
        "value":"fendom"
        },
        {
        "name":"Fingering",
        "value":"fingering"
        },
        {
        "name":"First Time",
        "value":"first-time"
        },
        {
        "name":"Footjob",
        "value":"footjob"
        },
        {
        "name":"Foursome",
        "value":"foursome"
        },
        {
        "name":"Full Color",
        "value":"full-color"
        },
        {
        "name":"Funny",
        "value":"funny"
        },
        {
        "name":"Furry",
        "value":"furry"
        },
        {
        "name":"Futanari",
        "value":"futanari"
        },
        {
        "name":"Game",
        "value":"game"
        },
        {
        "name":"Gangbang",
        "value":"gangbang"
        },
        {
        "name":"Garter Belts",
        "value":"garter-belts"
        },
        {
        "name":"Gender Bender",
        "value":"gender-bender"
        },
        {
        "name":"Ghen Tuông",
        "value":"ghen-tuong"
        },
        {
        "name":"Gia Đình",
        "value":"gia-dinh"
        },
        {
        "name":"Giả Tưởng",
        "value":"gia-tuong"
        },
        {
        "name":"Giáo Viên",
        "value":"giao-vien"
        },
        {
        "name":"Giật Gân",
        "value":"giat-gan"
        },
        {
        "name":"Giới Giải Trí",
        "value":"gioi-giai-tri"
        },
        {
        "name":"Girl Love",
        "value":"girl-love"
        },
        {
        "name":"Girllove",
        "value":"girllove"
        },
        {
        "name":"Glasses",
        "value":"glasses"
        },
        {
        "name":"Góa Phụ",
        "value":"goa-phu"
        },
        {
        "name":"Gothic Lolita",
        "value":"gothic-lolita"
        },
        {
        "name":"Group",
        "value":"group"
        },
        {
        "name":"Guideverse",
        "value":"guideverse"
        },
        {
        "name":"Guro",
        "value":"guro"
        },
        {
        "name":"Gyaru",
        "value":"gyaru"
        },
        {
        "name":"Hài Romance",
        "value":"hai-romance"
        },
        {
        "name":"Hairy",
        "value":"hairy"
        },
        {
        "name":"Hãm Hiếp",
        "value":"ham-hiep"
        },
        {
        "name":"Handjob",
        "value":"handjob"
        },
        {
        "name":"Hàng Xóm",
        "value":"hang-xom"
        },
        {
        "name":"Hành Động",
        "value":"hanh-dong"
        },
        {
        "name":"Hấp Diêm",
        "value":"hap-diem"
        },
        {
        "name":"Hardcode",
        "value":"hardcode"
        },
        {
        "name":"Harem",
        "value":"harem"
        },
        {
        "name":"Harem Ngược",
        "value":"harem-nguoc"
        },
        {
        "name":"Hầu Gái",
        "value":"hau-gai"
        },
        {
        "name":"Hậu Môn",
        "value":"hau-mon"
        },
        {
        "name":"Hệ Thống",
        "value":"he-thong"
        },
        {
        "name":"Hentai",
        "value":"hentai"
        },
        {
        "name":"Hentai 3D",
        "value":"hentai-3d"
        },
        {
        "name":"Hentai Cube",
        "value":"hentai-cube"
        },
        {
        "name":"Hentai Không Che",
        "value":"hentai-khong-che"
        },
        {
        "name":"Hentai Lxmanga",
        "value":"hentai-lxmanga"
        },
        {
        "name":"Hentai Màu",
        "value":"hentai-mau"
        },
        {
        "name":"Hentaiayame",
        "value":"hentaiayame"
        },
        {
        "name":"Hentaicb",
        "value":"hentaicb"
        },
        {
        "name":"Hentaihvn",
        "value":"hentaihvn"
        },
        {
        "name":"Hentaivl",
        "value":"hentaivl"
        },
        {
        "name":"Hentaivn",
        "value":"hentaivn"
        },
        {
        "name":"Hentaivnx",
        "value":"hentaivnx"
        },
        {
        "name":"Hentaiz",
        "value":"hentaiz"
        },
        {
        "name":"Hiện Đại",
        "value":"hien-dai"
        },
        {
        "name":"Hiện Thực",
        "value":"hien-thuc"
        },
        {
        "name":"Hiếp Dâm",
        "value":"hiep-dam"
        },
        {
        "name":"Hiểu Lầm",
        "value":"hieu-lam"
        },
        {
        "name":"Historical",
        "value":"historical"
        },
        {
        "name":"Họ Hàng",
        "value":"ho-hang"
        },
        {
        "name":"Hoàng Gia",
        "value":"hoang-gia"
        },
        {
        "name":"Hôn Nhân Hợp Đồng",
        "value":"hon-nhan-hop-dong"
        },
        {
        "name":"Hồng Hài Nhi",
        "value":"hong-hai-nhi"
        },
        {
        "name":"Horror",
        "value":"horror"
        },
        {
        "name":"Housewife",
        "value":"housewife"
        },
        {
        "name":"Huge Ass",
        "value":"huge-ass"
        },
        {
        "name":"Huge Boobs",
        "value":"huge-boobs"
        },
        {
        "name":"Humiliation",
        "value":"humiliation"
        },
        {
        "name":"Huyền Bí",
        "value":"huyen-bi"
        },
        {
        "name":"Huyền Huyễn",
        "value":"huyen-huyen"
        },
        {
        "name":"Idol",
        "value":"idol"
        },
        {
        "name":"Ihentai",
        "value":"ihentai"
        },
        {
        "name":"Imouto",
        "value":"imouto"
        },
        {
        "name":"Incest",
        "value":"incest"
        },
        {
        "name":"Incomplete",
        "value":"incomplete"
        },
        {
        "name":"Insect",
        "value":"insect"
        },
        {
        "name":"Inseki",
        "value":"inseki"
        },
        {
        "name":"Josei",
        "value":"josei"
        },
        {
        "name":"Joseon",
        "value":"joseon"
        },
        {
        "name":"Khổ Dâm",
        "value":"kho-dam"
        },
        {
        "name":"Khoa Học Viễn Tưởng",
        "value":"khoa-hoc-vien-tuong"
        },
        {
        "name":"Khoảng Cách Tuổi Tác",
        "value":"khoang-cach-tuoi-tac"
        },
        {
        "name":"Không Che",
        "value":"khong-che"
        },
        {
        "name":"Không Ntr",
        "value":"khong-ntr"
        },
        {
        "name":"Kimono",
        "value":"kimono"
        },
        {
        "name":"Kinh Dị",
        "value":"kinh-di%cc%a3"
        },
        {
        "name":"Kissing",
        "value":"kissing"
        },
        {
        "name":"Kogal",
        "value":"kogal"
        },
        {
        "name":"Kuudere",
        "value":"kuudere"
        },
        {
        "name":"Lão Gìa Dâm",
        "value":"lao-gia-dam"
        },
        {
        "name":"Lếu Lều",
        "value":"leu-leu"
        },
        {
        "name":"Lingerie",
        "value":"lingerie"
        },
        {
        "name":"Lỗ Nhị",
        "value":"lo-nhi"
        },
        {
        "name":"Loạn Luân",
        "value":"loan-luan"
        },
        {
        "name":"Loạn Luân Chị Em",
        "value":"loan-luan-chi-em"
        },
        {
        "name":"Loli",
        "value":"loli"
        },
        {
        "name":"Lolicon",
        "value":"lolicon"
        },
        {
        "name":"Lxhentai",
        "value":"lxhentai"
        },
        {
        "name":"Lxnovel",
        "value":"lxnovel"
        },
        {
        "name":"Ma Cà Rồng",
        "value":"ma-ca-rong"
        },
        {
        "name":"Ma Giới",
        "value":"ma-gioi"
        },
        {
        "name":"Magic",
        "value":"magic"
        },
        {
        "name":"Maid",
        "value":"maid"
        },
        {
        "name":"Maids",
        "value":"maids"
        },
        {
        "name":"Mang Thai",
        "value":"mang-thai"
        },
        {
        "name":"Manga",
        "value":"manga"
        },
        {
        "name":"Manhua",
        "value":"manhua"
        },
        {
        "name":"Manhwa",
        "value":"manhwa"
        },
        {
        "name":"Masturbation",
        "value":"masturbation"
        },
        {
        "name":"Mắt Kính",
        "value":"mat-kinh"
        },
        {
        "name":"Mất Trí Nhớ",
        "value":"mat-tri-nho"
        },
        {
        "name":"Mature",
        "value":"mature"
        },
        {
        "name":"Mẹ Con",
        "value":"me-con"
        },
        {
        "name":"Miko",
        "value":"miko"
        },
        {
        "name":"Milf",
        "value":"milf"
        },
        {
        "name":"Milfs",
        "value":"milfs"
        },
        {
        "name":"Mimihentai",
        "value":"mimihentai"
        },
        {
        "name":"Mind Break",
        "value":"mind-break"
        },
        {
        "name":"Mind Control",
        "value":"mind-control"
        },
        {
        "name":"Mizugi",
        "value":"mizugi"
        },
        {
        "name":"Mối Quan Hệ Bí Mật",
        "value":"moi-quan-he-bi-mat"
        },
        {
        "name":"Mối Quan Hệ Hợp Đồng",
        "value":"moi-quan-he-hop-dong"
        },
        {
        "name":"Mối Quan Hệ Tình Cảm Phức Tạp",
        "value":"moi-quan-he-tinh-cam-phuc-tap"
        },
        {
        "name":"Mối Tình Đầu",
        "value":"moi-tinh-dau"
        },
        {
        "name":"Mối Tình Tay Ba",
        "value":"moi-tinh-tay-ba"
        },
        {
        "name":"Mông To",
        "value":"mong-to"
        },
        {
        "name":"Monster",
        "value":"monster"
        },
        {
        "name":"Monster Girl",
        "value":"monster-girl"
        },
        {
        "name":"Monstergirl",
        "value":"monstergirl"
        },
        {
        "name":"Mori Sinrisk",
        "value":"mori-sinrisk"
        },
        {
        "name":"Mother",
        "value":"mother"
        },
        {
        "name":"Mystery",
        "value":"mystery"
        },
        {
        "name":"Nakadashi",
        "value":"nakadashi"
        },
        {
        "name":"Nam Hối Hận",
        "value":"nam-hoi-han"
        },
        {
        "name":"Nam Sinh",
        "value":"nam-sinh"
        },
        {
        "name":"Nặng Đô",
        "value":"nang-do"
        },
        {
        "name":"Năng Lực Siêu Nhiên",
        "value":"nang-luc-sieu-nhien"
        },
        {
        "name":"Net Truyen Hentai",
        "value":"net-truyen-hentai"
        },
        {
        "name":"Netori",
        "value":"netori"
        },
        {
        "name":"Ngoài Trời",
        "value":"ngoai-troi"
        },
        {
        "name":"Ngoài Trời/Công Cộng",
        "value":"ngoai-troi-cong-cong"
        },
        {
        "name":"Ngôn Tình",
        "value":"ngon-tinh"
        },
        {
        "name":"Ngọt",
        "value":"ngot"
        },
        {
        "name":"Ngực Lớn",
        "value":"nguc-lon"
        },
        {
        "name":"Ngực Nhỏ",
        "value":"nguc-nho"
        },
        {
        "name":"Ngược",
        "value":"nguoc"
        },
        {
        "name":"Người Đàn Ông Ám Ảnh",
        "value":"nguoi-dan-ong-am-anh"
        },
        {
        "name":"Người Đàn Ông Nhỏ Tuổi",
        "value":"nguoi-dan-ong-nho-tuoi"
        },
        {
        "name":"Người Nổi Tiếng",
        "value":"nguoi-noi-tieng"
        },
        {
        "name":"Người Phụ Nữ Lớn Tuổi",
        "value":"nguoi-phu-nu-lon-tuoi"
        },
        {
        "name":"Người Thú",
        "value":"nguoi-thu"
        },
        {
        "name":"Nhóm",
        "value":"nhom"
        },
        {
        "name":"Niên Hạ",
        "value":"nien-ha"
        },
        {
        "name":"Nô Lệ",
        "value":"no-le"
        },
        {
        "name":"No Sex",
        "value":"no-sex"
        },
        {
        "name":"Ntr",
        "value":"ntr"
        },
        {
        "name":"Nữ Cường",
        "value":"nu-cuong"
        },
        {
        "name":"Nữ Đơn Phương",
        "value":"nu-don-phuong"
        },
        {
        "name":"Nữ Giả Nam",
        "value":"nu-gia-nam"
        },
        {
        "name":"Nữ Sinh",
        "value":"nu-sinh"
        },
        {
        "name":"Nun",
        "value":"nun"
        },
        {
        "name":"Nurse",
        "value":"nurse"
        },
        {
        "name":"Oan Gia",
        "value":"oan-gia"
        },
        {
        "name":"Office Lady",
        "value":"office-lady"
        },
        {
        "name":"Old Man",
        "value":"old-man"
        },
        {
        "name":"One Shot",
        "value":"one-shot"
        },
        {
        "name":"Oneshot",
        "value":"oneshot"
        },
        {
        "name":"Oral",
        "value":"oral"
        },
        {
        "name":"Orgasm Denial",
        "value":"orgasm-denial"
        },
        {
        "name":"Osananajimi",
        "value":"osananajimi"
        },
        {
        "name":"Paizuri",
        "value":"paizuri"
        },
        {
        "name":"Pantyhose",
        "value":"pantyhose"
        },
        {
        "name":"Phản Bội",
        "value":"phan-boi"
        },
        {
        "name":"Phiêu Lưu",
        "value":"phieu-luu"
        },
        {
        "name":"Phong Cách Phương Đông",
        "value":"phong-cach-phuong-dong"
        },
        {
        "name":"Ponytail",
        "value":"ponytail"
        },
        {
        "name":"Pregnant",
        "value":"pregnant"
        },
        {
        "name":"Psychological",
        "value":"psychological"
        },
        {
        "name":"Quái Vật",
        "value":"quai-vat"
        },
        {
        "name":"Quản Giáo",
        "value":"quan-giao"
        },
        {
        "name":"Quan Hệ Hôn Nhân Hợp Đồng",
        "value":"quan-he-hon-nhan-hop-dong"
        },
        {
        "name":"Ranh Mãnh",
        "value":"ranh-manh"
        },
        {
        "name":"Rape",
        "value":"rape"
        },
        {
        "name":"Raw",
        "value":"raw"
        },
        {
        "name":"Rimjob",
        "value":"rimjob"
        },
        {
        "name":"Robot",
        "value":"robot"
        },
        {
        "name":"Romanc",
        "value":"romanc"
        },
        {
        "name":"Romance",
        "value":"romance"
        },
        {
        "name":"Romanceg",
        "value":"romanceg"
        },
        {
        "name":"Romcom",
        "value":"romcom"
        },
        {
        "name":"Rphang",
        "value":"rphang"
        },
        {
        "name":"Ryona",
        "value":"ryona"
        },
        {
        "name":"Say Xỉn",
        "value":"say-xin"
        },
        {
        "name":"Sayhentai",
        "value":"sayhentai"
        },
        {
        "name":"Scat",
        "value":"scat"
        },
        {
        "name":"School Life",
        "value":"school-life"
        },
        {
        "name":"School Uniform",
        "value":"school-uniform"
        },
        {
        "name":"Schoolboy Outfit",
        "value":"schoolboy-outfit"
        },
        {
        "name":"Schoolgirl",
        "value":"schoolgirl"
        },
        {
        "name":"Schoolgirl Outfit",
        "value":"schoolgirl-outfit"
        },
        {
        "name":"Schoolgirl Uniform",
        "value":"schoolgirl-uniform"
        },
        {
        "name":"Sci-Fi",
        "value":"sci-fi"
        },
        {
        "name":"Sếch Tàn Bạo",
        "value":"sech-tan-bao"
        },
        {
        "name":"Seinen",
        "value":"seinen"
        },
        {
        "name":"Series",
        "value":"series"
        },
        {
        "name":"Sex Bạo",
        "value":"sex-bao"
        },
        {
        "name":"Sex Toys",
        "value":"sex-toys"
        },
        {
        "name":"Short",
        "value":"short"
        },
        {
        "name":"Short Hentai",
        "value":"short-hentai"
        },
        {
        "name":"Shota",
        "value":"shota"
        },
        {
        "name":"Shotacon",
        "value":"shotacon"
        },
        {
        "name":"Shoujo",
        "value":"shoujo"
        },
        {
        "name":"Shounen",
        "value":"shounen"
        },
        {
        "name":"Siêu Nhiên",
        "value":"sieu-nhien"
        },
        {
        "name":"Siscon",
        "value":"siscon"
        },
        {
        "name":"Sister",
        "value":"sister"
        },
        {
        "name":"Sixty-Nine",
        "value":"sixty-nine"
        },
        {
        "name":"Slave",
        "value":"slave"
        },
        {
        "name":"Sleeping",
        "value":"sleeping"
        },
        {
        "name":"Slice of Life",
        "value":"slice-of-life"
        },
        {
        "name":"Small Boobs",
        "value":"small-boobs"
        },
        {
        "name":"Small Breasts",
        "value":"small-breasts"
        },
        {
        "name":"Small Penis",
        "value":"small-penis"
        },
        {
        "name":"Smut",
        "value":"smut"
        },
        {
        "name":"Socks",
        "value":"socks"
        },
        {
        "name":"Soft Yaoi",
        "value":"soft-yaoi"
        },
        {
        "name":"Soft Yuri",
        "value":"soft-yuri"
        },
        {
        "name":"Sole Female",
        "value":"sole-female"
        },
        {
        "name":"Sole Male",
        "value":"sole-male"
        },
        {
        "name":"Sport",
        "value":"sport"
        },
        {
        "name":"Sports",
        "value":"sports"
        },
        {
        "name":"Squirting",
        "value":"squirting"
        },
        {
        "name":"Stocking",
        "value":"stocking"
        },
        {
        "name":"Stockings",
        "value":"stockings"
        },
        {
        "name":"Story Arc",
        "value":"story-arc"
        },
        {
        "name":"Sự Nhỉ Nhục",
        "value":"su-nhi-nhuc"
        },
        {
        "name":"Succubus",
        "value":"succubus"
        },
        {
        "name":"Supernatural",
        "value":"supernatural"
        },
        {
        "name":"Sweating",
        "value":"sweating"
        },
        {
        "name":"Swimsuit",
        "value":"swimsuit"
        },
        {
        "name":"Swinging",
        "value":"swinging"
        },
        {
        "name":"T",
        "value":"t"
        },
        {
        "name":"Tài Phiệt",
        "value":"tai-phiet"
        },
        {
        "name":"Tall Girl",
        "value":"tall-girl"
        },
        {
        "name":"Tam Giác Tình Yêu",
        "value":"tam-giac-tinh-yeu"
        },
        {
        "name":"Teacher",
        "value":"teacher"
        },
        {
        "name":"Tentacle",
        "value":"tentacle"
        },
        {
        "name":"Tentacles",
        "value":"tentacles"
        },
        {
        "name":"Thanh Xuân Vườn Trường",
        "value":"thanh-xuan-vuon-truong"
        },
        {
        "name":"Thế Giới Abo",
        "value":"the-gioi-abo"
        },
        {
        "name":"Three Some",
        "value":"three-some"
        },
        {
        "name":"Thủ Dâm",
        "value":"thu-dam"
        },
        {
        "name":"Thú Nhân",
        "value":"thu-nhan"
        },
        {
        "name":"Thú Vật",
        "value":"thu-vat"
        },
        {
        "name":"Tì",
        "value":"ti"
        },
        {
        "name":"Time Stop",
        "value":"time-stop"
        },
        {
        "name":"Tình C",
        "value":"tinh-c"
        },
        {
        "name":"Tình Cảm",
        "value":"tinh-cam"
        },
        {
        "name":"Tình Tay Ba",
        "value":"tinh-tay-ba"
        },
        {
        "name":"Tình Yêu Bị Cấm Đoán",
        "value":"tinh-yeu-bi-cam-doan"
        },
        {
        "name":"Tình Yêu Công Sở",
        "value":"tinh-yeu-cong-so"
        },
        {
        "name":"Tình Yêu Hợp Đồng",
        "value":"tinh-yeu-hop-dong"
        },
        {
        "name":"Tomboy",
        "value":"tomboy"
        },
        {
        "name":"Tổng Tài",
        "value":"tong-tai"
        },
        {
        "name":"Toys",
        "value":"toys"
        },
        {
        "name":"Trả Thù",
        "value":"tra-thu"
        },
        {
        "name":"Tracksuit",
        "value":"tracksuit"
        },
        {
        "name":"Tragedy",
        "value":"tragedy"
        },
        {
        "name":"Transformation",
        "value":"transformation"
        },
        {
        "name":"Trap",
        "value":"trap"
        },
        {
        "name":"Trinh Thám",
        "value":"trinh-tham"
        },
        {
        "name":"Trọng Sinh",
        "value":"trong-sinh"
        },
        {
        "name":"Trường Học",
        "value":"truong-hoc"
        },
        {
        "name":"Truy",
        "value":"truy"
        },
        {
        "name":"Truy Thê",
        "value":"truy-the"
        },
        {
        "name":"Truyện",
        "value":"truyen"
        },
        {
        "name":"Truyện Dà",
        "value":"truyen-da"
        },
        {
        "name":"Truyện Dài",
        "value":"truyen-dai"
        },
        {
        "name":"Truyện M",
        "value":"truyen-m"
        },
        {
        "name":"Truyện Màu",
        "value":"truyen-mau"
        },
        {
        "name":"Truyện Ngắn",
        "value":"truyen-ngan"
        },
        {
        "name":"Truyện Ngọt",
        "value":"truyen-ngot"
        },
        {
        "name":"Truyện T",
        "value":"truyen-t"
        },
        {
        "name":"Truyện Tranh",
        "value":"truyen-tranh"
        },
        {
        "name":"Truyện Tranh 1",
        "value":"truyen-tranh-1"
        },
        {
        "name":"Truyện Tranh 18+",
        "value":"truyen-tranh-18"
        },
        {
        "name":"Truyện Việt",
        "value":"truyen-viet"
        },
        {
        "name":"Truyenvn",
        "value":"truyenvn"
        },
        {
        "name":"Tsundere",
        "value":"tsundere"
        },
        {
        "name":"Từ Bạn Thành Yêu",
        "value":"tu-ban-thanh-yeu"
        },
        {
        "name":"Tù Nhân",
        "value":"tu-nhan"
        },
        {
        "name":"Tự Sướng",
        "value":"tu-suong"
        },
        {
        "name":"Twins",
        "value":"twins"
        },
        {
        "name":"Twintails",
        "value":"twintails"
        },
        {
        "name":"Ugly Bastard",
        "value":"ugly-bastard"
        },
        {
        "name":"Uncensored",
        "value":"uncensored"
        },
        {
        "name":"V",
        "value":"v"
        },
        {
        "name":"Văn Phòng",
        "value":"van-phong"
        },
        {
        "name":"Vanilla",
        "value":"vanilla"
        },
        {
        "name":"Vét Máng",
        "value":"vet-mang"
        },
        {
        "name":"Vếu To",
        "value":"veu-to"
        },
        {
        "name":"Virgin",
        "value":"virgin"
        },
        {
        "name":"Virginity",
        "value":"virginity"
        },
        {
        "name":"Virgins",
        "value":"virgins"
        },
        {
        "name":"Vợ Của Sư Phụ",
        "value":"vo-cua-su-phu"
        },
        {
        "name":"Vườn Trường",
        "value":"vuon-truong"
        },
        {
        "name":"Webtoon",
        "value":"webtoon"
        },
        {
        "name":"X-Ray",
        "value":"x-ray"
        },
        {
        "name":"Xã Hội Đen",
        "value":"xa-hoi-den"
        },
        {
        "name":"Xúc Tua",
        "value":"xuc-tua"
        },
        {
        "name":"Xuyên Không",
        "value":"xuyen-khong"
        },
        {
        "name":"Y Tá",
        "value":"y-ta"
        },
        {
        "name":"Yandere",
        "value":"yandere"
        },
        {
        "name":"Yaoi",
        "value":"yaoi"
        },
        {
        "name":"Yêu Hận Đan Xen",
        "value":"yeu-han-dan-xen"
        },
        {
        "name":"Yuri",
        "value":"yuri"
        }
        ],
        "default":"16"
        }
        ]
        return [SourceFilter (**item )for item in data ]

    name ='truyenhentaiz_vi'
    display_name ='TruyenHentaiz'
    base_url ='https://truyenhentaiz.net'
    language ='vi'
    requests_per_minute =180 


SOURCE =GeneratedGenericSource

"""Puente de contrato para adaptadores que conservan metodos v3."""

import inspect
from collections.abc import Mapping
from typing import Any

from nyanko_api.sources.contract import Paginated, SourceFilter, SourcePreference


def _arguments(method: Any, page: int, filters: Mapping[str, Any] | None) -> dict[str, Any]:
    parameters = inspect.signature(method).parameters
    arguments: dict[str, Any] = {}
    if "page" in parameters:
        arguments["page"] = page
    if "filters" in parameters:
        arguments["filters"] = filters
    if "limit" in parameters:
        arguments["limit"] = 20
    return arguments


def _paginated(value: Any, has_more: bool) -> Paginated:
    if isinstance(value, Paginated):
        return value
    if isinstance(value, dict):
        items = value.get("items", value.get("results", []))
        has_more = bool(value.get("has_more", value.get("has_next_page", has_more)))
    else:
        items = value or []
    return Paginated(items=list(items), has_more=has_more and bool(items))


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
    class SourceV4(legacy_source):
        async def get_filters(self) -> list[SourceFilter]:
            getter = getattr(super(), "get_filters", None)
            if not getter:
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
            arguments = _arguments(method, page, filters)
            return _paginated(
                await method(query, **arguments),
                "page" in inspect.signature(method).parameters,
            )

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
