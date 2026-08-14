from __future__ import annotations


class MadaraDetailsSource :
    pass 


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


class PandachaikaSource (MadaraDetailsSource ):
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
        # El tag de idioma necesita su prefijo: "spanish" a secas no filtra y la API
        # responde 0 resultados; con "language:spanish" si devuelve el catalogo.
        ("tags",f"language:{self .search_language }"if self .search_language else ""),
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
        # Mismo prefijo que en browse: sin el, el filtro de idioma no casa con nada.
        tags =[f"language:{self .search_language }"]if self .search_language else []
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
    name ='pandachaika_es'
    display_name ='PandaChaika'
    base_url ='https://panda.chaika.moe'
    language ='es'
    requests_per_minute =60 
    content_warning ='nsfw'
    image_headers ={'Referer':'https://panda.chaika.moe/'}


SOURCE =PandachaikaSource

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
