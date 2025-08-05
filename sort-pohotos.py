#!/usr/bin/env python3
import os
import time
import json
import subprocess
import re
import argparse
import requests
import shutil
from datetime import datetime
from collections import defaultdict
from multiprocessing import Process, Queue, Lock, Manager
from queue import Empty
from typing import List, Tuple, Dict, Optional


IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.heic', '.pdf'}
VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv'}
ALL_EXTS = IMAGE_EXTS | VIDEO_EXTS
EXTS = tuple(ALL_EXTS)

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "location")
CACHE_LOC_FILE = os.path.join(CACHE_DIR, "location_cache.json")
CACHE_ALIAS_FILE = os.path.join(CACHE_DIR, "location_aliases.json")
CACHE_KEYS_FILE = os.path.join(CACHE_DIR, "service_keys.json")

SERVICES = {
    'Nominatim':    ['Nominatim', 'OpenCage', 'LocationIQ'],
    'OpenCage':     ['OpenCage', 'Nominatim', 'LocationIQ'],
    'LocationIQ':   ['LocationIQ', 'Nominatim', 'OpenCage']
}

SERVICE_KEYS = {
    # you can store the keys in the cache with the "-k" option
    'OpenCage': 'abcdef12345',
    'LocationIQ': 'wxyz98765432',
}

LOCATION_ALIAS = {
}

def gps_string_to_decimal(gps_str: str, precision: int, debug: bool = False) -> float:
    pattern = r"(\d+)\s+deg\s+(\d+)'\s+([\d.]+)\"\s+([NSEW])"
    match = re.match(pattern, gps_str)
    if not match:
        raise ValueError(f"Invalid GPS format: {gps_str}")
    degrees, minutes, seconds, direction = match.groups()
    decimal = float(degrees) + float(minutes)/60 + float(seconds)/3600
    if direction in ['S', 'W']:
        decimal *= -1
    result = round(decimal, precision)
    if debug:
        print(f"  Decimal conversion: {gps_str} -> {result}")
    return result

def extract_coordinates_and_dates(file_list: List[str], precision: int, 
                                  debug: bool = False) -> List[Tuple[str, float, float, str]]:
    if not file_list:
        return []

    try:
        cmd = ["exiftool", "-j", "-DateTimeOriginal", "-CreateDate", "-CreationDate", "-GPSDateStamp", "-GPSDateTime", "-GPSLatitude", "-GPSLongitude"] + file_list
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        metadata_list = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print("Failed to run exiftool:", e)
        return []

    results = []
    for item in metadata_list:
        source_file = item.get("SourceFile")
        lat_str = item.get("GPSLatitude")
        lon_str = item.get("GPSLongitude")
        # get creation date from metadata
        date_str_1 = item.get("CreationDate") or item.get("CreateDate") or item.get("DateTimeOriginal") or item.get("GPSDateStamp") or item.get("GPSDateTime")
        date_str_1 = date_str_1.split()[0].replace(':','-')
        # get creation date form OS
        stat = os.stat(source_file)
        try:
            ts = stat.st_birthtime  # macOS
        except AttributeError:
            ts = stat.st_ctime      # fallback for Unix (not true creation time)
        # print(f"DEBUG TS=ts")
        dt = datetime.fromtimestamp(ts)
        # print(f"DEBUG DT={dt}")
        date_str_2 = dt.strftime("%Y-%m-%d").split()[0]
        # use the earlist date between maetadata and OS
        date_str = min(date_str_1, date_str_2)
        # print (f"DEBUG [{date_str_1}] [{date_str_2}] [{date_str}]")
                
        if lat_str and lon_str and date_str:
            try:
                if debug:
                    print(f"Parsing: {source_file}")
                    print(f"  Raw GPS: {lat_str}, {lon_str}, {date_str}")
                lat = gps_string_to_decimal(lat_str, precision, debug=debug)
                lon = gps_string_to_decimal(lon_str, precision, debug=debug)
                date = date_str.split()[0].replace(":", "-")  # 'YYYY:MM:DD' -> 'YYYY-MM-DD'
                results.append((source_file, lat, lon, date))
            except Exception as e:
                print(f"  Error parsing metadata for {source_file}: {e}")
    return results

def collect_media_files(paths: List[str], recursive: bool = False, debug: bool = False, 
                        verbose: bool = False) -> List[str]:
    collected_files = []
    for path in paths:
        if os.path.isdir(path):
            if debug:
                print(f"Scanning directory: {path}")
            walker = os.walk(path) if recursive else [(path, [], os.listdir(path))]
            for root, dirs, files in walker:
                matched = 0
                for f in files:
                    full_path = os.path.join(root, f)
                    if f.lower().endswith(EXTS):
                        collected_files.append(full_path)
                        matched += 1
                if verbose and matched:
                    print(f"  {matched} valid files found in {root}")
        elif os.path.isfile(path) and path.lower().endswith(EXTS):
            collected_files.append(path)
    if verbose:
        print(f"Total media files collected: {len(collected_files)}")
    return collected_files

def load_json(path: str) -> dict:
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_json(obj_proxy: dict, path: str) -> None:
    obj = dict(obj_proxy)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def clean_cache() -> None:
    if os.path.exists(CACHE_LOC_FILE):
        os.remove(CACHE_LOC_FILE)
        print(f"Cache cleared: {CACHE_LOC_FILE}")

def cache_key(lat: float, lon: float) -> str:
    return f"{round(lat, 4)},{round(lon, 4)}"

def reverse_geocode(lat: float, lon: float, services: List[str], lock: Lock, 
                    last_request_time: float) -> Tuple[str, float]:
    last_location = None
    for service in services:
        with lock:
            now = time.time()
            elapsed = now - last_request_time
            if elapsed < 1:
                time.sleep(1 - elapsed)
            last_request_time = time.time()

        try:
            if service == 'Nominatim':
                url = f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lon}"
                headers = {'User-Agent': 'metadata-reverse-geocoder'}
                r = requests.get(url, headers=headers)
            elif service == 'OpenCage':
                key = SERVICE_KEYS['OpenCage']
                url = f"https://api.opencagedata.com/geocode/v1/json?q={lat}+{lon}&key={key}"
                r = requests.get(url)
            elif service == 'LocationIQ':
                key = SERVICE_KEYS['LocationIQ']
                url = f"https://us1.locationiq.com/v1/reverse.php?key={key}&lat={lat}&lon={lon}&format=json"
                r = requests.get(url)
            else:
                continue  # Skip unknown service

            if not r.ok:
                continue

            data = r.json()
            if service == 'Nominatim':
                addr = data.get('address', {})
                location = ", ".join(filter(None, [
                    addr.get('road', ''),
                    addr.get('suburb', ''),
                    addr.get('city', '') or addr.get('town', '') or addr.get('village', ''),
                    addr.get('state', ''),
                    addr.get('country_code', '').upper()
                ]))
            elif service == 'OpenCage':
                components = data['results'][0]['components']
                location = ", ".join(filter(None, [
                    components.get('road', ''),
                    components.get('suburb', ''),
                    components.get('city', ''),
                    components.get('state', ''),
                    components.get('country_code', '').upper()
                ]))
            elif service == 'LocationIQ':
                addr = data.get('address', {})
                location = ", ".join(filter(None, [
                    addr.get('road', ''),
                    addr.get('suburb', ''),
                    addr.get('city', '') or addr.get('town', '') or addr.get('village', ''),
                    addr.get('state', ''),
                    addr.get('country_code', '').upper()
                ]))
            else:
                location = "Unknown Location"
        except Exception:
            location = "Unknown Location"

        # Accept result only if it has at least 3 commas
        location.replace(', , ',', ').strip(", ")
        ncommas = location.count(",")
        if ncommas > (0 if last_location is None else last_location.count(",")):
            last_location = location
        if ncommas >= 3:
            break;

    # Return the last attempted location (even if suboptimal)
    # Respect location alias
    ll = last_location.lower()
    if ll in LOCATION_ALIAS:
        last_location = LOCATION_ALIAS[ll]
    return last_location, last_request_time

def lookup_location_cached(lat: float, lon: float, preferred_service: str, 
                           lock: Lock, last_request_time: float, cache: dict, 
                           debug: bool = False) -> Tuple[str, float]:
    key = cache_key(lat, lon)
    if key in cache:
        location = cache[key]
        location = LOCATION_ALIAS.get(location.lower(), location)
        if debug:
            print(f"  Cache hit: {key} -> {location}")
        return location, last_request_time
    location, last_request_time = reverse_geocode(lat, lon, SERVICES[preferred_service], 
                                                  lock, last_request_time)
    location = LOCATION_ALIAS.get(location.lower(), location)
    if location != "Unknown Location":
        cache[key] = location
    if debug:
        print(f"  Reverse geocoded: ({lat}, {lon}) -> {location}")
    return location, last_request_time

def gps_to_location(req_queue: Queue, res_queue: Queue, service: str, lock: Lock, 
                    cache: dict, debug: bool) -> None:
    last_request_time = 0
    while True:
        try:
            file, lat, lon, date = req_queue.get(timeout=1)
        except Empty:
            continue
        location, last_request_time = lookup_location_cached(lat, lon, service, lock, last_request_time, cache, debug)
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        res_queue.put((file, lat, lon, date, location, timestamp))

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Group and organize media files by GPS and capture date.")
    parser.add_argument("inputs", nargs="+", help="List of folders and/or files")
    parser.add_argument(
        "-r", "--recursive", 
        action="store_true", 
        help="Recursively scan directories.")
    parser.add_argument(
        "-s", "--service", 
        choices=["Nominatim", "OpenCage", "LocationIQ"], 
        default="Nominatim",
        help="The rever geolocation service to use.")
    parser.add_argument(
        "-p", "--precision",
        type=int,
        default=4,
        help="Precision (decimal places) of the decimal GPS coordinates (default: 4)"
    )
    parser.add_argument(
        "-n", "--dry-run", 
        action="store_true", 
        help="Dry run: do not move files.")
    parser.add_argument(
        "-l", "--list", 
        action="store_true", 
        help="List cache contents.")
    parser.add_argument(
        "-c", "--clean-cache", 
        action="store_true", 
        help="Clear the location cache.")
    parser.add_argument(
        "-k", "--key",
        action="append",
        metavar="SERVICE:KEY",
        help="API key for a geolocation service. Format: Service:Key (e.g., OpenCage:abc123)."
    )
    parser.add_argument(
        "-a", "--alias",
        action="append",
        metavar="SOURCE=DEST",
        help="Defines SOURCE as an alias to DEST. Uses DEST."
    )
    parser.add_argument(
        "-v", "--verbose", 
        action="store_true", 
        help="Print output per file")
    parser.add_argument(
        "-d", "--debug", 
        action="store_true", 
        help="Print debug information")
    return parser.parse_args()

def main():
    args = parse_args()
    if args.clean_cache:
        clean_cache()
    
    manager = Manager()
    # Load persisted lcations, keys and aliases
    CACHE = manager.dict(load_json(CACHE_LOC_FILE))
    SERVICE_KEYS.update(load_json(CACHE_KEYS_FILE))
    LOCATION_ALIAS = manager.dict(load_json(CACHE_ALIAS_FILE))
    
    if args.key:
        for entry in args.key:
            if ':' not in entry:
                print(f"Invalid key format: '{entry}'. Expected format is Service:Key.")
                continue
            service, key = entry.split(':', 1)
            service = service.strip()
            key = key.strip()
            if service not in SERVICE_KEYS:
                print(f"Unsupported service '{service}'. Supported: {', '.join(SERVICE_KEYS)}")
                continue
            SERVICE_KEYS[service] = key
            print (SERVICE_KEYS)
    if args.alias:
        for entry in args.alias:
            if '=' not in entry:
                print(f"Invalid alias format: '{entry}'. Expected format is 'Source=Dest'.")
                continue
            source, dest = entry.split('=', 1)
            source = source.strip().lower()
            dest = dest.strip()
            LOCATION_ALIAS[source] = dest
    
    request_queue = Queue()
    result_queue = Queue()
    lock = Lock()

    worker = Process(target=gps_to_location, args=(request_queue, result_queue, args.service, lock, CACHE, args.debug))
    worker.start()
    
    # process media files
    media_files = collect_media_files(args.inputs, recursive=args.recursive, debug=args.debug, verbose=args.verbose)
    gps_data = extract_coordinates_and_dates(media_files, args.precision, debug=args.debug)
    
    # if at least one meida file has GPS data/info
    if gps_data:
    
        for item in gps_data:
            request_queue.put(item)
    
        results = []
        total = len(gps_data)
        while len(results) < total:
            try:
                result = result_queue.get(timeout=10)
                results.append(result)
                if args.verbose or args.debug:
                    print(f"\rProcessed {len(results)}/{total}", end='', flush=True)
            except Empty:
                print("Timeout waiting for result.")
                break
        print()
    
        # Group by (date, location)
        groups = defaultdict(list)
        for file, lat, lon, date, location, timestamp in results:
            group_key = (date, location)
            groups[group_key].append(file)
    
        for (date, location), files in groups.items():
            folder_name = re.sub(r'[\\/:"*?<>|]+', "_", f"{date} {location}".strip())            
            for file in files:
                original_dir = os.path.dirname(file)
                target_dir = os.path.join(original_dir, folder_name)
                dest_path = os.path.join(target_dir, os.path.basename(file))
                if args.verbose or args.dry_run:
                    print(f"{file} ->\n\t-> {dest_path}")
                if not args.dry_run:
                    try:
                        os.makedirs(target_dir, exist_ok=True)
                        shutil.move(file, dest_path)
                    except Exception as e:
                        print(f"Failed to move {file}: {e}")
    else:
        print("No GPS/date metadata found.")

    worker.terminate()
    worker.join()
    save_json(CACHE, CACHE_LOC_FILE)
    save_json(LOCATION_ALIAS, CACHE_ALIAS_FILE)
    save_json(SERVICE_KEYS, CACHE_KEYS_FILE)
    
    if args.list:    
        print("\n\nCACHED_LOCATIONS:")
        for k in CACHE:
            print(f"{k} -> {CACHE[k]}")
        print("\n\nSERVICE KEYS:")
        for k in SERVICE_KEYS:
            print(f"{k} -> {SERVICE_KEYS[k]}")
        print("\n\nLOCATION ALIAS:")
        for k in LOCATION_ALIAS:
            print(f"{k} -> {LOCATION_ALIAS[k]}")

if __name__ == "__main__":
    main()
