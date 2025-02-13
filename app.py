from flask import Flask, render_template, request, jsonify, send_from_directory
from HdRezkaApi import *
import os
import requests
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from requests.adapters import HTTPAdapter
import time

app = Flask(__name__)
# app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Update CORS to allow your Vercel domain
CORS(
    app, resources={r"/*": {"origins": "*"}}
)  # For testing, we'll allow all origins temporarily

# For Vercel, we need to handle the root path differently
if os.environ.get("VERCEL_ENV") == "production":
    app.config["STATIC_FOLDER"] = "/tmp"
    SUBTITLE_DIR = "/tmp/subtitles"
else:
    SUBTITLE_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "/static/subtitles"
    )

if not os.path.exists(SUBTITLE_DIR):
    os.makedirs(SUBTITLE_DIR, mode=0o755)


# Add logging
@app.after_request
def after_request(response):
    print(f"Request: {request.method} {request.url}")
    print(f"Response Status: {response.status}")
    if response.status_code != 200:
        print(f"Response Data: {response.get_data(as_text=True)}")
    return response


@app.errorhandler(404)
def not_found_error(error):
    return jsonify({"success": False, "error": "Resource not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"success": False, "error": "Internal server error"}), 500


@app.route("/")
def index():
    return render_template("index.html")


# List of fallback domains
HDREZKA_DOMAINS = [
    "https://hdrezka.ag",
    "https://hdrezka.me",
    "https://rezka.ag",
    "https://kinopub.me",
]


def try_search_with_fallback(query, find_all=True):

    for domain in HDREZKA_DOMAINS:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Connection": "keep-alive",
            }
            print(f"Trying domain: {domain}")
            rezka = HdRezkaSearch(
                domain,
                {
                    "http": "http://brd-customer-hl_17133699-zone-datacenter_proxy1:zmswb3g2byzf@brd.superproxy.io:33335",
                    "https": "http://brd-customer-hl_17133699-zone-datacenter_proxy1:zmswb3g2byzf@brd.superproxy.io:33335",
                },
                headers,
            )
            results = rezka(query, find_all=find_all)
            if results:
                return results
        except Exception as e:
            print(f"Error with domain {domain}: {str(e)}")
            continue

    raise Exception("Unable to access HdRezka through any available domains")


@app.route("/search", methods=["POST"])
def search():
    try:
        query = request.form.get("query")
        content_type = request.form.get("content_type", "all")

        print(f"Search Query: {query}")
        print(f"Content Type: {content_type}")

        if not query:
            return jsonify({"success": False, "error": "No query provided"}), 400

        results = try_search_with_fallback(query, find_all=True)
        matching_result = None

        for page in results:
            for result in page:
                if content_type == "all" or result.get("type") == content_type:
                    matching_result = result
                    break
            if matching_result:
                break

        if not matching_result:
            return jsonify({"success": False, "error": f"No {content_type} found"})

        url = matching_result["url"]
        rezka = HdRezkaApi(
            url,
            proxy={
                "http": "http://brd-customer-hl_17133699-zone-datacenter_proxy1:zmswb3g2byzf@brd.superproxy.io:33335",
                "https": "http://brd-customer-hl_17133699-zone-datacenter_proxy1:zmswb3g2byzf@brd.superproxy.io:33335",
            },
        )

        if rezka.type == "tv_series":
            # TV series handling remains the same
            series_info = rezka.seriesInfo["Оригинал (+субтитры)"]
            num_seasons = len(series_info["seasons"])
            episodes_per_season = {
                season: len(episodes)
                for season, episodes in series_info["episodes"].items()
            }
            return jsonify(
                {
                    "success": True,
                    "type": "tv_series",
                    "movie_name": query,
                    "thumbnail": rezka.thumbnail,
                    "rating": rezka.rating.value,
                    "translation_id": "238",
                    "num_seasons": num_seasons,
                    "episodes_per_season": episodes_per_season,
                }
            )
        else:
            stream = rezka.getStream(translation="238")("1080p")
            stream_2 = rezka.getStream(translation="238")
            subtitle_filename = None

            if (
                hasattr(stream_2, "subtitles")
                and hasattr(stream_2.subtitles, "subtitles")
                and "en" in stream_2.subtitles.subtitles
            ):
                subtitles_url = stream_2.subtitles.subtitles["en"]["link"]
                response = requests.get(subtitles_url)
                if response.status_code == 200:
                    subtitle_filename = f"{query}_subtitles.vtt"
                    subtitle_path = os.path.join(SUBTITLE_DIR, subtitle_filename)
                    os.makedirs(os.path.dirname(subtitle_path), exist_ok=True)
                    with open(subtitle_path, "wb") as f:
                        f.write(response.content)

            return jsonify(
                {
                    "success": True,
                    "type": "movie",
                    "movie_name": query,
                    "thumbnail": rezka.thumbnail,
                    "rating": rezka.rating.value,
                    "stream_url": stream,
                    "subtitle_filename": subtitle_filename,
                }
            )

    except Exception as e:
        print(f"Search Error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/episodes", methods=["GET"])
def get_episodes():
    season = request.args.get("season")
    translation_id = request.args.get("translation_id")
    query = request.args.get("query")

    try:
        results = try_search_with_fallback(query)
        if not results:
            return jsonify({"success": False, "error": "Content not found"})

        url = results[0]["url"]
        rezka = HdRezkaApi(url)

        # Get episodes for the specified season
        series_info = rezka.seriesInfo["Оригинал (+субтитры)"]
        if not season in series_info["episodes"]:
            return jsonify({"success": False, "error": "Season not found"})

        # Return list of episode numbers for the season
        episodes = list(series_info["episodes"][int(season)].keys())
        return jsonify({"success": True, "episodes": episodes})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/stream", methods=["GET"])
def get_stream():
    season = request.args.get("season")
    episode = request.args.get("episode")
    query = request.args.get("query")
    content_type = request.args.get("content_type")

    try:
        results = try_search_with_fallback(query, find_all=True)
        matching_result = None

        # Search through pages to find first matching result
        for page in results:
            for result in page:
                if content_type == "all" or result.get("type") == content_type:
                    matching_result = result
                    break
            if matching_result:
                break

        if not matching_result:
            return jsonify({"success": False, "error": f"No {content_type} found"})

        url = matching_result["url"]
        rezka = HdRezkaApi(url)

        # Handle TV series
        if content_type == "tv_series":
            stream = rezka.getStream(translation="238", season=season, episode=episode)(
                "1080p"
            )
        # Handle movies
        else:
            stream = rezka.getStream(translation="238")("1080p")

        # Get subtitles for either type
        stream_2 = (
            rezka.getStream(translation="238", season=season, episode=episode)
            if content_type == "tv_series"
            else rezka.getStream(translation="238")
        )
        subtitle_filename = None

        if (
            hasattr(stream_2, "subtitles")
            and hasattr(stream_2.subtitles, "subtitles")
            and "en" in stream_2.subtitles.subtitles
        ):
            subtitles_url = stream_2.subtitles.subtitles["en"]["link"]
            os.makedirs("static/subtitles", exist_ok=True)
            response = requests.get(subtitles_url)
            if response.status_code == 200:
                subtitle_filename = f"{query}_{'s' + season + 'e' + episode if content_type == 'tv_series' else ''}_subtitles.vtt"
                with open(f"static/subtitles/{subtitle_filename}", "wb") as f:
                    f.write(response.content)

        return jsonify(
            {
                "success": True,
                "stream_url": stream,
                "subtitle_filename": subtitle_filename,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/static/subtitles/<path:filename>")
def serve_subtitle(filename):
    if ".." in filename or filename.startswith("/"):
        return jsonify({"success": False, "error": "Invalid filename"}), 400
    return send_from_directory("static/subtitles", filename, mimetype="text/vtt")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
