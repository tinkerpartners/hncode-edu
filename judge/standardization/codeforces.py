"""Read official rating for a Codeforces problem matched by a reviewer."""

import re

import requests
from django.core.cache import cache

SOURCE = re.compile(
    r"https://codeforces\.com/(?:(?:problemset/problem/(\d{1,8})/)|(?:contest/(\d{1,8})/problem/))([A-Z][A-Z0-9]{0,5})/?"
)
CACHE_KEY = "standardization-codeforces-ratings-v1"


def parse_source(value):
    match = SOURCE.fullmatch(value)
    if match:
        return f"{int(match[1] or match[2])}/{match[3]}"
    raise ValueError("Dùng liên kết bài chính thức trên codeforces.com.")


def get_rating(key):
    ratings = cache.get(CACHE_KEY)
    if ratings is None:
        response = requests.get(
            "https://codeforces.com/api/problemset.problems", timeout=(3, 15)
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "OK":
            raise ValueError("Codeforces API returned a failed result")
        ratings = {
            f"{item['contestId']}/{item['index']}": item["rating"]
            for item in data["result"]["problems"]
            if "contestId" in item and isinstance(item.get("rating"), int)
        }
        cache.set(CACHE_KEY, ratings, timeout=86400)
    return ratings.get(key)
