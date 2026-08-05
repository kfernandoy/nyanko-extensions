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


"""Adaptador de OniSaga: catalogo y capitulos detras de componentes Livewire."""

_ONISAGA_READER_TOKEN =re .compile (r"""readerToken["']?\s*:\s*["']([^"']+)["']""")
_ONISAGA_PAGE_ORDER =re .compile (r"""["']?order["']?\s*:\s*(\d+)""")
_ONISAGA_CHAPTER_NUMBER =re .compile (r"Chapter\s+([\d.]+)")
_ONISAGA_RELATIVE =re .compile (r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago")
_ONISAGA_ORIGIN =re .compile (r"(Japanese|Korean|Chinese|English)",re .I )
_ONISAGA_YEAR =re .compile (r"^\d{4}$")
_ONISAGA_RATING =re .compile (r"(\d)\.0(?=[/ ])")
_ONISAGA_INTERPUNCT =re .compile (r"\s*·\s*")
_ONISAGA_LANGS ={
"en":"EN","fr":"FR","ja":"JA","pt-BR":"PT-BR",
"pt":"PT","es-419":"ES-LA","es":"ES",
}
_ONISAGA_ALL_LANGS =("EN","FR","JA","PT-BR","PT","ES-LA","ES")
_ONISAGA_TYPES =(
("","All"),("MANGA","Manga"),("MANHWA","Manhwa"),("MANHUA","Manhua"),
("NOVEL","Novel"),("ONE-SHOT","One-Shot"),("DOUJINSHI","Doujinshi"),
)
_ONISAGA_STATUSES =(
("","All"),("ongoing","Ongoing"),("completed","Completed"),
("hiatus","Hiatus"),("releasing","Releasing"),
)
_ONISAGA_SORTS =(
("created_at","Newest"),("view","Most Viewed"),("release_date","Release Date"),
("like_count","Top Rated (Likes)"),("title","Name A-Z"),
("vote_average","Top Rated (Score)"),("fan_favorites","Fan Favorites"),
)
_ONISAGA_MIN_CHAPTERS =(("","Any"),("10","10+"),("50","50+"),("100","100+"),("200","200+"))
_ONISAGA_TYPE_BADGES =("manga","manhwa","manhua","shounen","seinen","shoujo","josei")
_ONISAGA_GENRES =(
("1","Action"),("61","Adaptation"),("67","Adult"),("6","Adventure"),("84","Aliens"),
("43","Avant Garde"),("78","Award Winning"),("31","Boys Love"),("2","Comedy"),
("90","Comics"),("59","Crazy MC"),("98","Crime"),("57","Demon"),("5","Demons"),
("79","Doujinshi"),("15","Drama"),("56","Dungeons"),("29","Ecchi"),("68","Erotica"),
("7","Fantasy"),("62","Full Color"),("46","Game"),("75","Gender Bender"),
("63","Genderswap"),("49","Genius MC"),("28","Girls Love"),("80","Gore"),
("42","Gourmet"),("37","Harem"),("76","Hentai"),("66","Historical"),("16","Horror"),
("3","Isekai"),("34","Iyashikei"),("35","Josei"),("38","Kids"),("70","Lolicon"),
("64","Long Strip"),("8","Magic"),("99","Magical Girls"),("41","Mahou Shoujo"),
("11","Martial Arts"),("45","Mature"),("36","Mecha"),("101","Medical"),
("17","Military"),("88","Monster Girls"),("81","Monsters"),("47","Murim"),
("30","Music"),("19","Mystery"),("54","Necromancer"),("55","Overpowered"),
("12","Parody"),("100","Philosophical"),("85","Post-Apocalyptic"),("18","Psychological"),
("52","Regression"),("48","Reincarnation"),("51","Revenge"),("44","Reverse Harem"),
("20","Romance"),("86","Samurai"),("21","School"),("24","School Life"),("13","Sci-Fi"),
("14","Seinen"),("82","Self-Published"),("77","Shotacon"),("27","Shoujo"),
("73","Shoujo Ai"),("4","Shounen"),("72","Shounen Ai"),("26","Slice of Life"),
("69","Smut"),("22","Space"),("32","Sports"),("9","Super Power"),("89","Superhero"),
("10","Supernatural"),("87","Survival"),("39","Suspense"),("50","System"),
("40","Thriller"),("23","Time Travel"),("58","Tower"),("25","Tragedy"),
("33","Vampire"),("53","Villain"),("60","Violence"),("65","Web Comic"),
("113","Wuxia"),("74","Yaoi"),("71","Yuri"),
)


def _onisaga_detach (node :_Node )->None :
    parent =node .parent 
    if parent is not None and node in parent .children :
        parent .children .remove (node )


def _onisaga_ancestor (node :_Node ,*classes :str )->_Node |None :
    current =node .parent 
    while current is not None :
        if all (current .has_class (name )for name in classes ):
            return current 
        current =current .parent 
    return None 


class OniSagaSource (MadaraSource ):
    """El sitio es Livewire: hay que reenviar snapshot y token en cada pagina."""

    image_delay_seconds =2.0 

    def __init__ (self ,fetcher :SourceFetcher |None =None )->None :
        super ().__init__ (fetcher )
        self ._state :tuple [str ,str ,str ]|None =None # (url, snapshot, token)
        self ._reader_token =""
        self ._last_image_at =0.0 

    @property 
    def language_code (self )->str |None :
        return _ONISAGA_LANGS .get (self .language )

    def get_preferences (self )->list [SourcePreference ]:
        return [
        SourcePreference ("pref_nsfw","Show NSFW / 18+ Content","checkbox",default =False ),
        SourcePreference ("pref_type","Type Filter","select",list (_ONISAGA_TYPES ),""),
        SourcePreference ("pref_status","Status Filter","select",list (_ONISAGA_STATUSES ),""),
        SourcePreference ("pref_rate_limit","Image Requests Limit","select",[
        ("1500","1 image per 1.50 seconds"),("1750","1 image per 1.75 seconds"),
        ("2000","1 image per 2.00 seconds"),("2250","1 image per 2.25 seconds"),
        ("2500","1 image per 2.50 seconds"),
        ],"2000"),
        ]

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("platform","Type","select",list (_ONISAGA_TYPES ),""),
        SourceFilter ("genre","Genres","tri_state",list (_ONISAGA_GENRES ),[]),
        SourceFilter ("status","Status","select",list (_ONISAGA_STATUSES ),""),
        SourceFilter ("min_chapters","Min Chapters","select",list (_ONISAGA_MIN_CHAPTERS ),""),
        SourceFilter ("group","Group","text",default =""),
        SourceFilter ("release_start","Release Start Date (YYYY-MM-DD)","text",default =""),
        SourceFilter ("release_end","Release End Date (YYYY-MM-DD)","text",default =""),
        SourceFilter ("sort","Sort","select",list (_ONISAGA_SORTS ),"view"),
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        updates =self ._updates (sort ="view"if kind =="popular"else "created_at")
        return await self ._livewire_page (f"{self .base_url }/browse",page ,updates )

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        query =query .strip ()
        url =f"{self .base_url }/search/{query }"if query else f"{self .base_url }/browse"
        return await self ._livewire_page (url ,page ,self ._updates_from_filters (filters or {}))

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        response =await self ._request ("GET",self ._manga_url (series_id ))
        response .raise_for_status ()
        root =_parse_html (response .text )
        return self ._details (root ,series_id )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        url =self ._manga_url (series_id )
        response =await self ._request ("GET",url )
        response .raise_for_status ()
        root =_parse_html (response .text )
        self ._strip_nsfw_overlay (root )
        state =self ._livewire_state (root ,"manga.chapter-list")
        if state is None :
            return []
        snapshot ,token =state 
        codes =[self .language_code ]if self .language_code else list (_ONISAGA_ALL_LANGS )
        result :list [SourceChapter ]=[]
        for code in codes :
            result .extend (await self ._chapters_for (url ,snapshot ,token ,code ,series_id ))
        unique =list ({chapter .source_id :chapter for chapter in result }.values ())
        unique .sort (key =lambda chapter :chapter .number or 0.0 ,reverse =True )
        return unique 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        url =urljoin (f"{self .base_url }/",chapter_id .lstrip ("/"))
        response =await self ._request ("GET",url )
        response .raise_for_status ()
        found =_ONISAGA_READER_TOKEN .search (response .text )
        if not found :
            raise SourceNotFoundError (f"{self .display_name }: la pagina no trae readerToken")
        self ._reader_token =found .group (1 )
        count =len (_ONISAGA_PAGE_ORDER .findall (response .text ))
        return [
        SourcePage (
        source_id =f"{chapter_id }#{index }",
        chapter_id =chapter_id ,
        index =index ,
        filename =f"{index }.jpg",
        source_name =self .name ,
        )
        for index in range (count )
        ]

    async def page_bytes (self ,page :SourcePage |str )->SourcePageContent :
        value =page .source_id if isinstance (page ,SourcePage )else str (page )
        chapter_id ,_ ,order =value .rpartition ("#")
        chapter_url =urljoin (f"{self .base_url }/",chapter_id .lstrip ("/"))
        image_url =await self ._image_url (chapter_url ,order )
        response =await self ._request (
        "GET",image_url ,headers ={"Referer":chapter_url },
        )
        response .raise_for_status ()
        return SourcePageContent (
        media_type =response .headers .get ("Content-Type","image/jpeg"),
        chunks =iter ([response .content ]),
        )

        # -------------------------------------------------------------- livewire
    async def _livewire_page (self ,url :str ,page :int ,updates :dict |None )->dict :
        state =self ._state if self ._state and self ._state [0 ]==url else None 
        if state is None :
            response =await self ._request ("GET",url )
            response .raise_for_status ()
            root =_parse_html (response .text )
            if page ==1 and updates is None :
                return self ._manga_list (root )
            found =self ._livewire_state (root ,"post-filter")
            if found is None :
                raise SourceNotFoundError (f"{self .display_name }: sin estado Livewire")
            state =(url ,found [0 ],found [1 ])
            self ._state =state 
        payload =await self ._livewire_call (
        url ,
        state [1 ],
        state [2 ],
        updates or self ._updates (),
        [{"type":"call","path":"","method":"gotoPage","params":[str (page )]}],
        )
        component =(payload .get ("components")or [{}])[0 ]
        if component .get ("snapshot"):
            self ._state =(url ,component ["snapshot"],state [2 ])
        html =((component .get ("effects")or {}).get ("html"))or ""
        return self ._manga_list (_parse_html (html ))

    async def _chapters_for (
    self ,url :str ,snapshot :str ,token :str ,code :str ,series_id :str ,
    )->list [SourceChapter ]:
        current ,previous ,chapters =snapshot ,0 ,[]
        while True :
            payload =await self ._livewire_call (
            url ,current ,token ,{"language":code },
            [{"type":"call","path":"","method":"loadMoreChapters","params":[]}],
            )
            component =(payload .get ("components")or [{}])[0 ]
            html =((component .get ("effects")or {}).get ("html"))or ""
            if not html :
                break 
            chapters =self ._chapters_from (
            _parse_html (html ),code ,self .language_code is None ,series_id ,
            )
            if len (chapters )<=previous :
                break 
            previous =len (chapters )
            if not component .get ("snapshot"):
                break 
            current =component ["snapshot"]
        return chapters 

    async def _livewire_call (
    self ,referer :str ,snapshot :str ,token :str ,updates :dict ,calls :list [dict ],
    )->dict :
        response =await self ._request (
        "POST",
        f"{self .base_url }/livewire/update",
        json ={
        "_token":token ,
        "components":[{"snapshot":snapshot ,"updates":updates ,"calls":calls }],
        },
        headers ={
        "X-Livewire":"",
        "Accept":"application/json",
        "X-Requested-With":"XMLHttpRequest",
        "Origin":self .base_url ,
        "Referer":referer .partition ("?")[0 ],
        },
        )
        response .raise_for_status ()
        return response .json ()or {}

    @staticmethod 
    def _livewire_state (root :_Node ,component :str )->tuple [str ,str ]|None :
        token =next (
        (
        node .attrs .get ("content","")
        for node in root .descendants ("meta")
        if node .attrs .get ("name")=="csrf-token"and node .attrs .get ("content","").strip ()
        ),
        "",
        )or next (
        (
        node .attrs .get ("value","")
        for node in root .descendants ("input")
        if node .attrs .get ("name")=="_token"and node .attrs .get ("value","").strip ()
        ),
        "",
        )
        if not token :
            return None 
        for node in root .descendants ():
            for key ,value in node .attrs .items ():
                if key .endswith ("snapshot")and component in value :
                    return value ,token 
        return None 

        # --------------------------------------------------------------- parsing
    def _manga_list (self ,root :_Node )->dict :
        items :list [SourceSeries ]=[]
        for card in root .descendants ("div"):
            if not (card .has_class ("relative")and card .has_class ("group")):
                continue 
            entry =self ._card (card )
            if entry is not None :
                items .append (entry )
        has_more =any (
        "nextPage"in value and "disabled"not in node .attrs 
        for node in root .descendants ()
        for key ,value in node .attrs .items ()
        if key =="wire:click"
        )
        return {"items":items ,"has_more":has_more }

    def _card (self ,card :_Node )->SourceSeries |None :
    # La preferencia de contenido 18+ no vuelve a la fuente: se mantiene oculta.
        if _first (card ,lambda node :node .tag =="span"and "18+"in node .text ())is not None :
            return None 
        anchor =_first (
        card ,lambda node :node .tag =="a"and "/manga/"in node .attrs .get ("href",""),
        )
        if anchor is None :
            return None 
        parts =[part for part in urlparse (
        urljoin (f"{self .base_url }/",anchor .attrs .get ("href","")),
        ).path .split ("/")if part ]
        if len (parts )<2 or parts [0 ].casefold ()!="manga":
            return None 
        heading =_first (
        card ,
        lambda node :"data-flux-heading"in node .attrs or node .tag in {"h3","h4"},
        )or _first (card ,lambda node :node .tag =="a"and node .attrs .get ("title"))
        heading =heading or anchor 
        title =heading .attrs .get ("title","").strip ()or heading .text ().strip ()
        if not title :
            return None 
        image =_first (
        card ,lambda node :node .tag =="img"and node .attrs .get ("alt","").strip (),
        )or _first (card ,lambda node :node .tag =="img")
        return SourceSeries (
        source_id =parts [1 ],
        title =title ,
        source_name =self .name ,
        cover_url =self ._image (image )if image is not None else None ,
        web_url =f"{self .base_url }/manga/{parts [1 ]}",
        )

    def _details (self ,root :_Node ,series_id :str )->SourceSeries :
        self ._strip_nsfw_overlay (root )
        heading =_first (root ,lambda node :node .tag =="h1")or _first (
        root ,lambda node :"data-flux-heading"in node .attrs ,
        )
        if heading is None :
            raise SourceNotFoundError (f"{self .display_name }: ficha sin titulo")
        badges =next (
        (
        node 
        for node in root .descendants ("div")
        if all (node .has_class (name )for name in 
        ("flex","items-center","gap-2","justify-center","mb-2"))
        ),
        None ,
        )
        info =next (
        (
        node 
        for node in root .descendants ("div")
        if node .has_class ("flex")and node .has_class ("flex-col")
        ),
        None ,
        )
        types =[
        text .capitalize ()
        for node in (badges .descendants ("div")if badges is not None else [])
        if "data-flux-badge"in node .attrs and (text :=node .text ().strip ().casefold ())
        in _ONISAGA_TYPE_BADGES 
        ]
        tags =[
        text 
        for node in (info .descendants ("a")if info is not None else [])
        if "/genre/"in node .attrs .get ("href","")and (text :=node .text ().strip ())
        ]
        summary =_first (root ,lambda node :node .tag =="p"and node .has_class ("leading-relaxed"))
        return SourceSeries (
        source_id =series_id ,
        title =heading .text ().strip (),
        source_name =self .name ,
        cover_url =None ,
        description =(summary .text ().strip ()if summary is not None else None )or None ,
        author =", ".join (
        text 
        for node in (info .descendants ("a")if info is not None else [])
        if "/author/"in node .attrs .get ("href","")and (text :=node .text ().strip ())
        )or None ,
        status =self ._status (root ),
        content_tags =tuple (types +tags ),
        web_url =self ._manga_url (series_id ),
        )

    def _chapters_from (
    self ,root :_Node ,code :str ,is_all :bool ,series_id :str ,
    )->list [SourceChapter ]:
        result :list [SourceChapter ]=[]
        for anchor in root .descendants ("a"):
            if not anchor .has_class ("gap-4"):
                continue 
            heading =_first (anchor ,lambda node :"data-flux-heading"in node .attrs )
            number =self ._chapter_number (anchor ,heading )
            href =anchor .attrs .get ("href","")
            if number is None or "/read/"not in href :
                continue 
            result .append (
            self ._chapter (href ,number ,code if is_all else "",series_id ,anchor ),
            )
        for dropdown in root .descendants ("ui-dropdown"):
            button =_first (dropdown ,lambda node :node .tag =="button")
            if button is None :
                continue 
            heading =_first (button ,lambda node :"data-flux-heading"in node .attrs )
            number =self ._chapter_number (button ,heading )
            if number is None :
                continue 
            unknown =1 
            for link in dropdown .descendants ("a"):
                if "data-flux-menu-item"not in link .attrs :
                    continue 
                href =link .attrs .get ("href","")
                if "/read/"not in href :
                    continue 
                label =_first (link ,lambda node :node .tag =="span"and node .has_class ("text-sm"))
                group =label .text ().strip ()if label is not None else ""
                if not group or group .casefold ()=="unknown group":
                    group =f"Unknown {unknown }"
                    unknown +=1 
                result .append (
                self ._chapter (
                href ,number ,f"{code } - {group }"if is_all else group ,series_id ,button ,
                )
                )
        return result 

    def _chapter (
    self ,href :str ,number :str ,scanlator :str ,series_id :str ,holder :_Node ,
    )->SourceChapter :
        text =_first (holder ,lambda node :node .tag =="p"and "data-flux-text"in node .attrs )
        details =[
        part 
        for part in _ONISAGA_INTERPUNCT .split (
        (text .text ().replace (" - "," · ")if text is not None else ""),
        )
        if part 
        ]
        stamp =next (
        (
        part 
        for part in details 
        if any (word in part .casefold ()for word in ("ago","today","yesterday"))
        ),
        "",
        )
        return SourceChapter (
        source_id =urlparse (urljoin (f"{self .base_url }/",href )).path .lstrip ("/"),
        title =f"Chapter {number }",
        series_id =series_id ,
        source_name =self .name ,
        number =self ._float (number ),
        language =self .language ,
        scanlator =scanlator ,
        uploaded_at =self ._relative_date (stamp ),
        )

        # -------------------------------------------------------------- internals
    async def _image_url (self ,chapter_url :str ,order :str )->str :
        import asyncio 
        import time 

        identifier =chapter_url .rstrip ("/").rsplit ("/",1 )[-1 ]
        api =f"{self .base_url }/api/chapter/{identifier }/page/{order }"
        for _ in range (3 ):
        # El propio Kotlin espacia estas llamadas para no comerse un 429.
            wait =self .image_delay_seconds -(time .monotonic ()-self ._last_image_at )
            if wait >0 :
                await asyncio .sleep (wait )
            self ._last_image_at =time .monotonic ()
            response =await self ._request (
            "GET",
            api ,
            headers ={
            "X-Reader-Token":self ._reader_token ,
            "Sec-Fetch-Mode":"cors",
            "Sec-Fetch-Site":"same-origin",
            "Referer":chapter_url ,
            },
            )
            headers =getattr (response ,"headers",None )or {}
            if headers .get ("x-reader-token-next"):
                self ._reader_token =headers ["x-reader-token-next"]
            if getattr (response ,"status_code",200 )==429 :
                continue 
            payload =response .json ()or {}
            if payload .get ("url"):
                return str (payload ["url"])
            refreshed =await self ._request ("GET",chapter_url )
            found =_ONISAGA_READER_TOKEN .search (refreshed .text )
            if not found :
                raise SourceNotFoundError (f"{self .display_name }: {payload .get ('message')}")
            self ._reader_token =found .group (1 )
        raise SourceNotFoundError (f"{self .display_name }: sin imagen tras 3 intentos")

    def _updates (self ,sort :str ="created_at")->dict :
        return {
        "platform":"","status":"","sort":sort ,"min_chapters":"",
        "group":None ,"release_start":None ,"release_end":None ,
        "genre":[],"excludeGenre":[],
        }

    def _updates_from_filters (self ,values :dict )->dict |None :
        chosen =values .get ("genre")or {}
        include =[key for key ,state in chosen .items ()if state =="include"]if isinstance (chosen ,dict )else []
        exclude =[key for key ,state in chosen .items ()if state =="exclude"]if isinstance (chosen ,dict )else []
        updates ={
        "platform":str (values .get ("platform")or ""),
        "status":str (values .get ("status")or ""),
        "sort":str (values .get ("sort")or "created_at"),
        "min_chapters":str (values .get ("min_chapters")or ""),
        "group":str (values ["group"]).strip ()or None if values .get ("group")else None ,
        "release_start":str (values ["release_start"]).strip ()or None if values .get ("release_start")else None ,
        "release_end":str (values ["release_end"]).strip ()or None if values .get ("release_end")else None ,
        "genre":include ,
        "excludeGenre":exclude ,
        }
        default =self ._updates ()
        return None if updates ==default else updates 

    def _manga_url (self ,series_id :str )->str :
        slug =series_id .rstrip ("/").rsplit ("/",1 )[-1 ]
        return f"{self .base_url }/manga/{slug }"

    def _image (self ,node :_Node )->str |None :
        value =(
        node .attrs .get ("data-src")
        or node .attrs .get ("data-lazy-src")
        or node .attrs .get ("src")
        or ""
        )
        if not value or value .startswith ("data:"):
            return None 
        return urljoin (f"{self .base_url }/",value )

    @staticmethod 
    def _strip_nsfw_overlay (root :_Node )->None :
        marker =_first (root ,lambda node :node .tag =="span"and "18+"in node .text ())
        if marker is None :
            return 
        overlay =_onisaga_ancestor (marker ,"absolute","inset-0","z-20")
        if overlay is not None :
            _onisaga_detach (overlay )

    @staticmethod 
    def _chapter_number (holder :_Node ,heading :_Node |None )->str |None :
        if heading is not None :
            text =heading .text ().replace ("Chapter ","").strip ()
            if text :
                return text 
        fallback =_first (holder ,lambda node :node .has_class ("w-10"))
        return fallback .text ().strip ()if fallback is not None else None 

    @staticmethod 
    def _status (root :_Node )->str |None :
        marker =_first (
        root ,
        lambda node :node .tag =="span"
        and any (
        child .tag =="span"and child .has_class ("size-1.5")
        for child in node .children 
        if isinstance (child ,_Node )
        ),
        )
        text =marker .text ().casefold ()if marker is not None else ""
        if not text :
            candidate =_first (
            root ,
            lambda node :node .tag =="span"
            and node .has_class ("inline-flex")
            and any (
            word in node .text ()
            for word in ("Completed","Ongoing","Hiatus","Cancelled")
            ),
            )
            text =candidate .text ().casefold ()if candidate is not None else ""
        for words ,value in (
        (("ongoing","releasing"),"ongoing"),
        (("completed",),"completed"),
        (("hiatus",),"hiatus"),
        (("cancelled","dropped"),"cancelled"),
        ):
            if any (word in text for word in words ):
                return value 
        return None 

    @staticmethod 
    def _float (value :str )->float |None :
        try :
            return float (value )
        except (TypeError ,ValueError ):
            return None 

    @staticmethod 
    def _relative_date (value :str )->str |None :
        from datetime import datetime ,timedelta 

        text =value .casefold ()
        now =datetime .now ().replace (microsecond =0 )
        if not text :
            return None 
        if "today"in text :
            return now .isoformat ()
        if "yesterday"in text :
            return (now -timedelta (days =1 )).isoformat ()
        found =_ONISAGA_RELATIVE .search (text )
        if not found :
            return None 
        amount ,unit =int (found .group (1 )),found .group (2 )
        spans ={
        "minute":timedelta (minutes =1 ),"hour":timedelta (hours =1 ),"day":timedelta (days =1 ),
        "week":timedelta (weeks =1 ),"month":timedelta (days =30 ),"year":timedelta (days =365 ),
        }
        return (now -spans [unit ]*amount ).isoformat ()


class GeneratedOniSagaSource (OniSagaSource ):
    name ='onisaga_es_419'
    display_name ='OniSaga'
    base_url ='https://onisaga.com'
    language ='es-419'
    requests_per_minute =240 
    content_warning ='mixed'
    image_headers ={'Referer':'https://onisaga.com/'}


SOURCE =GeneratedOniSagaSource

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
