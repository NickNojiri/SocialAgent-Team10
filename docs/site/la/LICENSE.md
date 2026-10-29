# Data in this folder

The 3D Los Angeles behind `spotbot.html` is built from these files by `docs/site/tools/build_la3d.py`.

- `buildings.bin`, `roads.bin`, `la.json`: derived from OpenStreetMap data, © OpenStreetMap contributors,
  available under the Open Database License (ODbL) 1.0: https://opendatacommons.org/licenses/odbl/1-0/.
  These derived files are made available under the same license. See https://www.openstreetmap.org/copyright.
- `height.bin`: elevation from AWS Terrain Tiles. United States 3DEP (formerly NED) and global GMTED2010
  and SRTM terrain data courtesy of the U.S. Geological Survey. Global ETOPO1 terrain data U.S. National
  Oceanic and Atmospheric Administration.
- `tex/`: CC0 textures from ambientCG (https://ambientcg.com).
- `spots.js`: places from SpotBot's labelled reels (`tools/build_spots.py`). Coordinates are from the reel's own
  location tag, or from Overture Maps places (CDLA-Permissive-2.0, © Overture Maps Foundation and contributors).
