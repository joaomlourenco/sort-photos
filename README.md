# sort-photos
Group and organize media files by capture date and GPS location.

# About
This si a personal project to manage my photos.
The script takes a list of folder or file names and…

1. read the metadata of the files in the given folders using the external tool [`exiftool`](https://exiftool.org) and extract the file creation time and the GPS coordinates is available.
2. apply a revers geolocation too map the GPS coordinates to the name of the place using one of these services: [`Nominatim`](https://nominatim.org), [`OpenCage`](https://opencagedata.com), or [`LocationIQ`](https://locationiq.com).
3. for each folder, group the files into by date and location and move them into a new sub-folder named `YYYY-MM-DD Loocation`

# Dependencies

## Python dependencies
Python `requests` module
```shell
pip install requests
```

## External tool dependencies
[`exiftool`](https://exiftool.org)

macos: `brew install exiftool`

linux: `apt-get install exiftool`

windows: download from the [`exiftool` web page](https://exiftool.org)

# Usage

```text
usage: sort-pohotos.py [-h] [-r] [-s {Nominatim,OpenCage,LocationIQ}]
                       [-p PRECISION] [-n] [-l] [-c] [-k SERVICE:KEY]
                       [-a SOURCE=DEST] [-v] [-d]
                       inputs [inputs ...]

Group and organize media files by GPS and capture date.

positional arguments:
  inputs                List of folders and/or files

options:
  -h, --help            show this help message and exit
  -r, --recursive       Recursively scan directories.
  -s, --service {Nominatim,OpenCage,LocationIQ}
                        The rever geolocation service to use.
  -p, --precision PRECISION
                        Precision (decimal places) of the decimal GPS
                        coordinates (default: 4)
  -n, --dry-run         Dry run: do not move files.
  -l, --list            List cache contents.
  -c, --clean-cache     Clear the location cache.
  -k, --key SERVICE:KEY
                        API key for a geolocation service. Format: Service:Key
                        (e.g., OpenCage:abc123).
  -a, --alias SOURCE=DEST
                        Defines SOURCE as an alias to DEST. Uses DEST.
                        (e.g., "Wrong Steet Name, My City, My Country=Right Steet Name, My City, My Country").
  -v, --verbose         Print output per file
  -d, --debug           Print debug information
```
