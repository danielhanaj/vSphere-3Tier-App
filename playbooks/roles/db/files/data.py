#!/usr/bin/env python3

# data.py - script used to produce database results over HTTP for simple sqlite3 database

import os
import sqlite3
from urllib.parse import parse_qs

conn = sqlite3.connect('/etc/httpd/db/clients.db')
curs = conn.cursor()

print("Content-type:text/plain; charset=utf-8\n\n")

form = parse_qs(os.getenv("QUERY_STRING", ""))
querystring = form.get("querystring", [None])[0]
if querystring is not None and querystring.strip() != "":
    queryval = "%" + querystring.strip() + "%"
    select = "SELECT Planet, DistanceFromSunAU, Moons, DiameterMiles, MoonDetails, StarDetails FROM planets WHERE Planet LIKE ? ORDER BY DistanceFromSunAU ASC"
    rows = curs.execute(select, (queryval,))
else:
    select = "SELECT Planet, DistanceFromSunAU, Moons, DiameterMiles, MoonDetails, StarDetails FROM planets ORDER BY DistanceFromSunAU ASC"
    rows = curs.execute(select)

for row in rows:
    if len(row) == 6:
        print("\t".join(str(item) for item in row))

conn.close()