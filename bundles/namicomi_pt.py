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


class MadaraDetailsSource :
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


"""Adaptador de NamiComi: API propia con relaciones y capitulos con acceso."""

_NAMICOMI_TAG_FILTERS =(
('content','Content',(
('drugs','Drugs'),
('gambling','Gambling'),
('gore','Gore'),
('mental-disorders','Mental Disorders'),
('physical-abuse','Physical Abuse'),
('racism','Racism'),
('self-harm','Self-harm'),
('sexual-abuse','Sexual Abuse'),
('verbal-abuse','Verbal Abuse'),
)),
('format','Format',(
('4-koma','4-Koma'),
('adaptation','Adaptation'),
('anthology','Anthology'),
('full-color','Full Color'),
('oneshot','Oneshot'),
('silent','Silent'),
)),
('genre','Genre',(
('action','Action'),
('adventure','Adventure'),
('boys-love',"Boys' Love"),
('comedy','Comedy'),
('crime','Crime'),
('drama','Drama'),
('fantasy','Fantasy'),
('girls-love',"Girls' Love"),
('historical','Historical'),
('horror','Horror'),
('isekai','Isekai'),
('mecha','Mecha'),
('medical','Medical'),
('mystery','Mystery'),
('philosophical','Philosophical'),
('psychological','Psychological'),
('romance','Romance'),
('sci-fi','Sci-Fi'),
('slice-of-life','Slice of Life'),
('sports','Sports'),
('superhero','Superhero'),
('thriller','Thriller'),
('tragedy','Tragedy'),
('wuxia','Wuxia'),
)),
('theme','Theme',(
('aliens','Aliens'),
('animals','Animals'),
('cooking','Cooking'),
('crossdressing','Crossdressing'),
('delinquents','Delinquents'),
('demons','Demons'),
('genderswap','Genderswap'),
('ghosts','Ghosts'),
('gyaru','Gyaru'),
('harem','Harem'),
('mafia','Mafia'),
('magic','Magic'),
('magical-girls','Magical Girls'),
('martial-arts','Martial Arts'),
('military','Military'),
('monster-girls','Monster Girls'),
('monsters','Monsters'),
('music','Music'),
('ninja','Ninja'),
('office-workers','Office Workers'),
('police','Police'),
('post-apocalyptic','Post-Apocalyptic'),
('reincarnation','Reincarnation'),
('reverse-harem','Reverse Harem'),
('samurai','Samurai'),
('school-life','School Life'),
('supernatural','Supernatural'),
('survival','Survival'),
('time-travel','Time Travel'),
('traditional-games','Traditional Games'),
('vampires','Vampires'),
('video-games','Video Games'),
('villainess','Villainess'),
('virtual-reality','Virtual Reality'),
('zombies','Zombies'),
)),
)
_NAMICOMI_TAG_NAMES ={
'4-koma':'4-Koma',
'action':'Action',
'adaptation':'Adaptation',
'adventure':'Adventure',
'aliens':'Aliens',
'animals':'Animals',
'anthology':'Anthology',
'boys-love':"Boys' Love",
'comedy':'Comedy',
'cooking':'Cooking',
'crime':'Crime',
'crossdressing':'Crossdressing',
'delinquents':'Delinquents',
'demons':'Demons',
'drama':'Drama',
'drugs':'Drugs',
'fantasy':'Fantasy',
'full-color':'Full Color',
'gambling':'Gambling',
'genderswap':'Genderswap',
'ghosts':'Ghosts',
'girls-love':"Girls' Love",
'gore':'Gore',
'gyaru':'Gyaru',
'harem':'Harem',
'historical':'Historical',
'horror':'Horror',
'isekai':'Isekai',
'mafia':'Mafia',
'magic':'Magic',
'magical-girls':'Magical Girls',
'martial-arts':'Martial Arts',
'mecha':'Mecha',
'medical':'Medical',
'mental-disorders':'Mental Disorders',
'military':'Military',
'monster-girls':'Monster Girls',
'monsters':'Monsters',
'music':'Music',
'mystery':'Mystery',
'ninja':'Ninja',
'office-workers':'Office Workers',
'oneshot':'Oneshot',
'philosophical':'Philosophical',
'physical-abuse':'Physical Abuse',
'police':'Police',
'post-apocalyptic':'Post-Apocalyptic',
'psychological':'Psychological',
'racism':'Racism',
'reincarnation':'Reincarnation',
'reverse-harem':'Reverse Harem',
'romance':'Romance',
'samurai':'Samurai',
'school-life':'School Life',
'sci-fi':'Sci-Fi',
'self-harm':'Self-harm',
'sexual-abuse':'Sexual Abuse',
'silent':'Silent',
'slice-of-life':'Slice of Life',
'sports':'Sports',
'superhero':'Superhero',
'supernatural':'Supernatural',
'survival':'Survival',
'thriller':'Thriller',
'time-travel':'Time Travel',
'traditional-games':'Traditional Games',
'tragedy':'Tragedy',
'vampires':'Vampires',
'verbal-abuse':'Verbal Abuse',
'video-games':'Video Games',
'villainess':'Villainess',
'virtual-reality':'Virtual Reality',
'wuxia':'Wuxia',
'zombies':'Zombies',
}

_NAMICOMI_API ="https://api.namicomi.com"
_NAMICOMI_CDN ="https://uploads.namicomi.com"
_NAMICOMI_LIMIT =20 
_NAMICOMI_LOCK ="🔒"
_NAMICOMI_EXT_LANGS ={
"zh-Hans":"zh-hans","zh-Hant":"zh-hant","pt-BR":"pt-br","pt":"pt-pt","es":"es-es",
}
_NAMICOMI_INCLUDES =("cover_art","organization","tag","primary_tag","secondary_tag")
_NAMICOMI_TAG_GROUPS =("content-warnings","format","genre","theme")
_NAMICOMI_STATUSES =(
("ongoing","Ongoing"),("completed","Completed"),
("hiatus","Hiatus"),("cancelled","Cancelled"),
)
_NAMICOMI_RATINGS =(("safe","Safe"),("restricted","Restricted"),("mature","Mature"))
_NAMICOMI_SORTS =(
("title","Alphabetic"),("chapterCount","Number of chapters"),
("followCount","Number of follows"),("reactions","Number of likes"),
("commentCount","Number of comments"),("publishedAt","Content created at"),
("views","Views"),("year","Year"),("rating","Rating"),
)
_NAMICOMI_LANG_NAMES ={
"en":"English","ja":"Japanese","ko":"Korean","zh":"Chinese","es":"Spanish",
"pt":"Portuguese","fr":"French","de":"German","it":"Italian","ru":"Russian",
"id":"Indonesian","th":"Thai","vi":"Vietnamese","ar":"Arabic","tr":"Turkish",
"pl":"Polish","nl":"Dutch","uk":"Ukrainian","fil":"Filipino","hi":"Hindi",
}


class NamiComiSource (MadaraDetailsSource ):
    """API tipo MangaDex: relaciones incluidas y capitulos con control de acceso."""

    @property 
    def ext_language (self )->str :
        return _NAMICOMI_EXT_LANGS .get (self .language ,self .language )

    def get_preferences (self )->list [SourcePreference ]:
        return [
        SourcePreference (
        f"thumbnailQuality_{self .ext_language }","Cover quality","select",
        [("","Original"),(".512.jpg","Medium"),(".256.jpg","Low")],"",
        ),
        SourcePreference (f"dataSaver_{self .ext_language }","Data saver","checkbox",default =False ),
        SourcePreference (
        f"showLockedChapters_{self .ext_language }","Show locked chapters",
        "checkbox",default =False ,
        ),
        ]

    def get_filters (self )->list [SourceFilter ]:
        return [
        SourceFilter ("hasAvailableChapters","Has available chapters","checkbox",default =False ),
        SourceFilter ("contentRatings","Content rating","multi_select",list (_NAMICOMI_RATINGS ),[]),
        SourceFilter ("publicationStatuses","Status","multi_select",list (_NAMICOMI_STATUSES ),[]),
        SourceFilter ("sort","Sort","select",list (_NAMICOMI_SORTS ),"publishedAt"),
        SourceFilter ("sortDirection","Sort direction","select",[
        ("desc","Descending"),("asc","Ascending"),
        ],"desc"),
        SourceFilter ("includedTagsMode","Included tags mode","select",[
        ("and","And"),("or","Or"),
        ],"and"),
        SourceFilter ("excludedTagsMode","Excluded tags mode","select",[
        ("and","And"),("or","Or"),
        ],"or"),
        *[
        SourceFilter (identifier ,label ,"tri_state",list (options ),[])
        for identifier ,label ,options in _NAMICOMI_TAG_FILTERS 
        ],
        ]

    async def browse (self ,kind :str ,page :int =1 ):
        if kind not in {"popular","latest"}:
            return {"items":[],"has_more":False }
        params =[
        (f"order[{'views'if kind =='popular'else 'publishedAt'}]","desc"),
        ("availableTranslatedLanguages[]",self .ext_language ),
        ("limit",str (_NAMICOMI_LIMIT )),
        ("offset",str (_NAMICOMI_LIMIT *(page -1 ))),
        *self ._includes (),
        ]
        return self ._manga_list (await self ._get (f"{_NAMICOMI_API }/title/search",params ))

    async def search (self ,query :str ,page :int =1 ,filters :dict |None =None ):
        query =query .strip ()
        if query .startswith ("https://"):
            parts =[part for part in urlparse (query ).path .split ("/")if part ]
            if urlparse (query ).netloc !=urlparse (self .base_url ).netloc or len (parts )<3 :
                raise ValueError ("URL no compatible")
            query =f"id:{parts [2 ]}"
        if query .startswith ("id:"):
            identifier =query [3 :].strip ()
            if not identifier :
                raise ValueError ("Identificador invalido")
            return self ._manga_list (
            await self ._get (
            f"{_NAMICOMI_API }/title/search",[("ids[]",identifier ),*self ._includes ()],
            )
            )
        values =filters or {}
        params :list [tuple [str ,str ]]=[
        ("limit",str (_NAMICOMI_LIMIT )),
        ("offset",str (_NAMICOMI_LIMIT *(page -1 ))),
        *self ._includes (),
        ]
        normalized =" ".join (query .split ())
        if normalized :
            params .append (("title",normalized ))
        if values .get ("hasAvailableChapters"):
            params .append (("hasAvailableChapters","true"))
            params .append (("availableTranslatedLanguages[]",self .ext_language ))
        params .extend (
        ("contentRatings[]",str (value ))for value in values .get ("contentRatings")or []
        )
        params .extend (
        ("publicationStatuses[]",str (value ))
        for value in values .get ("publicationStatuses")or []
        )
        params .append ((
        f"order[{values .get ('sort')or 'publishedAt'}]",
        "asc"if str (values .get ("sortDirection"))=="asc"else "desc",
        ))
        params .append (("includedTagsMode",str (values .get ("includedTagsMode")or "and")))
        params .append (("excludedTagsMode",str (values .get ("excludedTagsMode")or "or")))
        for identifier ,_ ,_ in _NAMICOMI_TAG_FILTERS :
            chosen =values .get (identifier )
            if not isinstance (chosen ,dict ):
                continue 
            params .extend (
            ("includedTags[]"if state =="include"else "excludedTags[]",str (tag ))
            for tag ,state in chosen .items ()
            if state in {"include","exclude"}
            )
        return self ._manga_list (await self ._get (f"{_NAMICOMI_API }/title/search",params ))

    async def details (self ,series :SourceSeries |str )->SourceSeries :
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        payload =await self ._get (f"{_NAMICOMI_API }/title/{series_id }",list (self ._includes ()))
        data =payload .get ("data")
        if not isinstance (data ,dict ):
            raise SourceNotFoundError (f"{self .display_name }: ficha no encontrada")
        return self ._manga (data )

    async def chapters (self ,series :SourceSeries |str )->list [SourceChapter ]:
        series_id =series .source_id if isinstance (series ,SourceSeries )else str (series )
        entries :list [dict ]=[]
        offset =0 
        while True :
            payload =await self ._get (f"{_NAMICOMI_API }/chapter",[
            ("titleId",series_id ),
            ("includes[]","organization"),
            ("limit","200"),
            ("offset",str (offset )),
            ("translatedLanguages[]",self .ext_language ),
            ("order[volume]","desc"),
            ("order[chapter]","desc"),
            ])
            entries .extend (
            item for item in payload .get ("data")or []if isinstance (item ,dict )
            )
            meta =payload .get ("meta")or {}
            limit ,current ,total =(
            int (meta .get ("limit")or 0 ),int (meta .get ("offset")or 0 ),int (meta .get ("total")or 0 ),
            )
            if limit +current >=total or not limit :
                break 
            offset =current +limit 
        if not entries :
            return []
            # El acceso a cada capitulo se consulta aparte, en tandas de 200.
        access :dict [str ,bool ]={}
        identifiers =[str (item .get ("id"))for item in entries ]
        for start in range (0 ,len (identifiers ),200 ):
            response =await self ._request (
            "POST",
            f"{_NAMICOMI_API }/gating/check",
            json ={
            "entities":[
            {"entityId":value ,"entityType":"chapter"}
            for value in identifiers [start :start +200 ]
            ],
            },
            )
            response .raise_for_status ()
            payload =response .json ()or {}
            data =payload .get ("data")
            if isinstance (data ,dict ):
                access .update ((data .get ("attributes")or {}).get ("map")or {})
        result :list [SourceChapter ]=[]
        for item in entries :
            identifier =str (item .get ("id"))
            if not access .get (identifier ):
                continue 
            result .append (self ._chapter (item ,series_id ))
        return result 

    async def pages (self ,chapter :SourceChapter |str )->list [SourcePage ]:
        chapter_id =chapter .source_id if isinstance (chapter ,SourceChapter )else str (chapter )
        payload =await self ._get (
        f"{_NAMICOMI_API }/images/chapter/{chapter_id }",[("newQualities","true")],
        )
        data =payload .get ("data")
        if not isinstance (data ,dict ):
            return []
        prefix =f"{data .get ('baseUrl')}/chapter/{chapter_id }/{data .get ('hash')}"
        images =data .get ("source")or []
        return [
        SourcePage (
        source_id =f"{prefix }/source/{image .get ('filename')}",
        chapter_id =chapter_id ,
        index =index ,
        filename =str (image .get ("filename")or f"{index }.jpg"),
        source_name =self .name ,
        )
        for index ,image in enumerate (images )
        if isinstance (image ,dict )and image .get ("filename")
        ]

        # -------------------------------------------------------------- internals
    async def _get (self ,url :str ,params :list [tuple [str ,str ]])->dict :
        response =await self ._request ("GET",url ,params =params )
        if getattr (response ,"status_code",200 )==204 :
            return {"data":[],"meta":{}}
        response .raise_for_status ()
        return response .json ()or {}

    @staticmethod 
    def _includes ()->list [tuple [str ,str ]]:
        return [("includes[]",value )for value in _NAMICOMI_INCLUDES ]

    def _manga_list (self ,payload :dict )->dict :
        meta =payload .get ("meta")or {}
        limit ,offset ,total =(
        int (meta .get ("limit")or 0 ),int (meta .get ("offset")or 0 ),int (meta .get ("total")or 0 ),
        )
        return {
        "items":[
        self ._manga (item )
        for item in payload .get ("data")or []
        if isinstance (item ,dict )
        ],
        "has_more":limit +offset <total ,
        }

    def _manga (self ,item :dict )->SourceSeries :
        attributes =item .get ("attributes")or {}
        relationships =[
        value for value in item .get ("relationships")or []if isinstance (value ,dict )
        ]
        titles =attributes .get ("title")or {}
        title =titles .get (self .ext_language )or next (iter (titles .values ()),"")
        descriptions =attributes .get ("description")or {}
        organizations =list (dict .fromkeys (
        str ((value .get ("attributes")or {}).get ("name"))
        for value in relationships 
        if value .get ("type")=="organization"and (value .get ("attributes")or {}).get ("name")
        ))
        cover =next (
        (
        (value .get ("attributes")or {}).get ("fileName")
        for value in relationships 
        if value .get ("type")=="cover_art"and (value .get ("attributes")or {}).get ("fileName")
        ),
        None ,
        )
        grouped :dict [str ,list [str ]]={}
        for value in relationships :
            if value .get ("type")not in {"tag","primary_tag","secondary_tag"}:
                continue 
            group =(value .get ("attributes")or {}).get ("group")
            name =_NAMICOMI_TAG_NAMES .get (str (value .get ("id")))
            if group and name :
                grouped .setdefault (str (group ),[]).append (name )
        tags =[name for group in _NAMICOMI_TAG_GROUPS for name in sorted (grouped .get (group ,[]))]
        rating =attributes .get ("contentRating")
        if rating and rating !="safe":
            tags .append (f"Content rating: {rating .capitalize ()}")
        original =attributes .get ("originalLanguage")
        if original :
            tags .append (_NAMICOMI_LANG_NAMES .get (original ,str (original ).upper ()))
        return SourceSeries (
        source_id =str (item .get ("id")),
        title =str (title ),
        source_name =self .name ,
        cover_url =f"{_NAMICOMI_CDN }/covers/{item .get ('id')}/{cover }"if cover else None ,
        description =str (
        descriptions .get (self .ext_language )or descriptions .get ("en")or "",
        )or None ,
        author =", ".join (organizations )or None ,
        status =self ._status (attributes .get ("publicationStatus")),
        content_tags =tuple (value for value in tags if value ),
        web_url =(
        f"{self .base_url }/{self .ext_language }/title/{item .get ('id')}"
        f"/{self ._slug (str (title ))}"
        ),
        )

    def _chapter (self ,item :dict ,series_id :str )->SourceChapter :
        attributes =item .get ("attributes")or {}
        parts :list [str ]=[]
        if attributes .get ("volume"):
            parts .append (f"Vol.{attributes ['volume']}")
        if attributes .get ("chapter"):
            parts .append (f"Ch.{attributes ['chapter']}")
        if attributes .get ("name"):
            if parts :
                parts .append ("-")
            parts .append (str (attributes ["name"]))
        return SourceChapter (
        source_id =str (item .get ("id")),
        title =" ".join (parts ),
        series_id =series_id ,
        source_name =self .name ,
        number =self ._float (attributes .get ("chapter")),
        language =self .language ,
        scanlator =", ".join (
        str ((value .get ("attributes")or {}).get ("name"))
        for value in item .get ("relationships")or []
        if isinstance (value ,dict )and value .get ("type")=="organization"
        and (value .get ("attributes")or {}).get ("name")
        ),
        uploaded_at =self ._date (attributes .get ("publishAt")),
        )

    @staticmethod 
    def _slug (title :str )->str :
        cleaned =re .sub (r"[^a-z0-9]+","-",title .strip ().casefold ())
        cleaned =re .sub (r"-+$","",cleaned )
        result =""
        for part in cleaned .split ("-"):
            candidate =f"{result }-{part }"if result else part 
            if result and len (candidate )>100 :
                break 
            result =candidate 
        return result 

    @staticmethod 
    def _status (value :Any )->str |None :
        return {
        "ongoing":"ongoing","completed":"completed",
        "hiatus":"hiatus","cancelled":"cancelled",
        }.get (str (value or ""))

    @staticmethod 
    def _float (value :Any )->float |None :
        try :
            return float (value )
        except (TypeError ,ValueError ):
            return None 

    @staticmethod 
    def _date (value :Any )->str |None :
        from datetime import datetime 

        if not value :
            return None 
        text =str (value )
        for pattern in ("%Y-%m-%dT%H:%M:%S+%f","%Y-%m-%dT%H:%M:%S"):
            try :
                return datetime .strptime (text ,pattern ).replace (microsecond =0 ).isoformat ()
            except ValueError :
                continue 
        return None 


class GeneratedNamiComiSource (NamiComiSource ):
    name ='namicomi_pt'
    display_name ='NamiComi'
    base_url ='https://namicomi.com'
    language ='pt'
    requests_per_minute =180 
    content_warning ='safe'
    image_headers ={'Referer':'https://namicomi.com/'}


SOURCE =GeneratedNamiComiSource

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
