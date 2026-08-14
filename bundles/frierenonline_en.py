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
SourceFilter ,
SourcePage ,
SourcePageContent ,
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
    return urljoin (base_url ,value )if value else ""


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
            return urljoin (base_url ,node .attrs [key ].strip ())
    candidates =[
    item .strip ().split ()[0 ]
    for item in node .attrs .get ("srcset","").split (",")
    if item .strip ()
    ]
    if candidates :
        return urljoin (base_url ,candidates [-1 ])
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
        return SourceSeries (
        source_id =series_id ,
        title =title ,
        source_name =self .name ,
        cover_url =_image_url (image ,str (response .url ))if image else None ,
        description =description or None ,
        author =", ".join (authors )or None ,
        artist =", ".join (artists )or None ,
        status =self ._madara_status (status_text ),
        content_tags =tuple (genres ),
        metadata =series .metadata if isinstance (series ,SourceSeries )else {},
        web_url =str (response .url ),
        )

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
        if not items and holder is not None :
            if self .use_new_chapter_endpoint :
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
            response .raise_for_status ()
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
        return await self .fetcher .request (method ,url ,**kwargs )


class GeneratedMadaraSource (MadaraDetailsSource ):

    def get_preferences (self )->list [SourcePreference ]:
    # Autogenerated via heuristic port
        data =[]
        return [SourcePreference (**item )for item in data ]

    def get_filters (self )->list [SourceFilter ]:
    # Autogenerated via heuristic port
        data =[]
        return [SourceFilter (**item )for item in data ]

    name ='frierenonline_en'
    display_name ='Frieren Online'
    base_url ='https://www.frieren.online'
    language ='en'
    manga_substring ='manga'
    load_more ='always'
    use_new_chapter_endpoint =False 
    chapter_url_suffix ='?style=list'
    supports_latest =False 
    requests_per_minute =60 
    pages_profile ='default'
    extra_headers ={}
    image_headers ={}


SOURCE =GeneratedMadaraSource

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
