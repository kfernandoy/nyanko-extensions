import json
import os
import re
from pathlib import Path

results_dir = Path("../Nyanko/.planning/extension-validation/results")
kt_src_dir = Path("../extensions-source-main/src")

# Map of Kotlin override prefix/name to Nyanko feature name
OVERRIDE_FEATURE_MAP = {
    "popularManga": "popular",
    "latestUpdates": "latest",
    "searchManga": "search",
    "mangaDetails": "details",
    "chapterList": "chapters",
    "pageList": "pages",
    "imageUrl": "cover",
    "imageRequest": "cover",
    "setupPreference": "preferences",
    "getFilterList": "filters",
    "client": "auth_webview", # could be cookies, interceptors
    "headers": "auth_webview",
    "fetchPopular": "popular",
    "fetchLatest": "latest",
    "fetchSearch": "search",
    "fetchChapterList": "chapters",
    "fetchPageList": "pages",
}

def map_feature(override_name):
    for key, feat in OVERRIDE_FEATURE_MAP.items():
        if override_name.startswith(key):
            return feat
    return None

updated_count = 0

for json_file in results_dir.glob("*.json"):
    data = json.loads(json_file.read_text("utf-8"))
    if data["status"] in ("BLOCKED_MAPPING", "RETIRED"):
        continue
        
    kmod = data.get("kotlin_module")
    kcls = data.get("kotlin_class")
    engine = data.get("engine")
    
    if not kmod or not kcls:
        continue
        
    kmod_path = kt_src_dir / kmod
    if not kmod_path.is_dir():
        # try replacing backslashes, wait kmod could use backslashes
        kmod_path = kt_src_dir.joinpath(*kmod.replace("\\", "/").split("/"))
        if not kmod_path.is_dir():
            continue
            
    # Read all kotlin files in the module
    kt_code = ""
    for kf in kmod_path.rglob("*.kt"):
        kt_code += kf.read_text("utf-8", errors="ignore") + "\n"
        
    # Find the class signature
    cls_match = re.search(f"class\s+{kcls}[^\(]*\([^)]*\)\s*:\s*([A-Za-z0-9_]+)", kt_code)
    if not cls_match:
        cls_match = re.search(f"class\s+{kcls}\s*:\s*([A-Za-z0-9_]+)", kt_code)
        
    base_class = cls_match.group(1) if cls_match else "Unknown"
    
    # Find all overrides
    overrides = set()
    for m in re.finditer(r"override\s+(?:suspend\s+)?(?:protected\s+|public\s+|private\s+)?fun\s+([A-Za-z0-9_]+)", kt_code):
        overrides.add(m.group(1))
    for m in re.finditer(r"override\s+(?:suspend\s+)?(?:protected\s+|public\s+|private\s+)?(?:lateinit\s+)?val\s+([A-Za-z0-9_]+)", kt_code):
        overrides.add(m.group(1))
        
    # Determine supported overrides for this engine based on generate.py (approximate)
    # If "custom", almost nothing is supported. We assume custom supports nothing.
    supported = set()
    if engine == "madara":
        supported = {"adultContentFilterOptions", "altName", "altNameSelector", "client", "chapterUrlSuffix", "dateFormat", "fetchGenres", "filterNonMangaItems", "genreConditionFilterOptions", "mangaDetailsSelectorArtist", "mangaDetailsSelectorAuthor", "mangaDetailsSelectorDescription", "mangaDetailsSelectorGenre", "mangaDetailsSelectorStatus", "mangaDetailsSelectorTag", "mangaDetailsSelectorThumbnail", "mangaDetailsSelectorTitle", "mangaSubString", "orderByFilterOptions", "sendViewCount", "seriesTypeSelector", "statusFilterOptions", "supportsLatest", "updatingRegex", "useLoadMoreRequest", "useNewChapterEndpoint", "pageListParseSelector", "capacity", "chapterUrlSelector", "headers", "imageRequest", "searchMangaNextPageSelector", "searchMangaSelector", "searchMangaUrlSelector"}
    elif engine == "mangathemesia":
        supported = {"altNamePrefix", "client", "dateFormat", "hasProjectPage", "mangaUrlDirectory", "pageSelector", "projectPageString", "sendViewCount", "seriesAltNameSelector", "seriesArtistSelector", "seriesAuthorSelector", "seriesDescriptionSelector", "seriesDetailsSelector", "seriesGenreSelector", "seriesStatusSelector", "seriesThumbnailSelector", "seriesTitleSelector", "seriesTypeSelector", "slugRegex", "supportsLatest", "capacity", "chapterUrlSelector", "headers", "imageRequest", "searchMangaNextPageSelector", "searchMangaSelector", "searchMangaUrlSelector"}
    # For others, we might need a broader list or simply treat generic engines as having no python overrides supported except those standard
    
    differences = data.get("differences", [])
    diff_names = [d.get("name") for d in differences if isinstance(d, dict)]
    
    changed = False
    
    if data.get("kotlin_base_class") != base_class:
        data["kotlin_base_class"] = base_class
        changed = True
        
    for ov in overrides:
        if ov not in supported:
            if ov not in diff_names:
                differences.append({"type": "unsupported_override", "name": ov, "reason": "Not supported by generic python engine script"})
                diff_names.append(ov)
                changed = True
            
            feat = map_feature(ov)
            if feat and data["features"].get(feat) == "PENDING":
                data["features"][feat] = "IMPLEMENTATION_REQUIRED"
                changed = True
                
    if changed:
        data["differences"] = differences
        json_file.write_text(json.dumps(data, indent=2), "utf-8")
        updated_count += 1
        
print(f"Phase A updated {updated_count} files.")
