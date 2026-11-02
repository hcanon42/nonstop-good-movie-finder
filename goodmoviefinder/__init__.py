"""Fetch Nonstop Kino program listings and rank them with Letterboxd.

The run starts in cli. lookup matches each program film, profile loads the
Letterboxd account, and session keeps the page the local server updates.
nonstop, letterboxd, and matching read the two sites. cache stores results.
program groups the listings. taste builds recommendations. html, account,
and layout render the pages. serve exposes them on localhost.
"""
