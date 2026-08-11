import requests
import re
import os
import model
# Normalize input
def normalize_key(text):
    return re.sub(r"[^a-z0-9]", "", text.strip().lower())

# Search for city/place (normal flow)
def get_country_from_geonames(city_name):
    url = os.environ["URL_SEARCHJSON"]  
    username = os.environ["USERNAME_GEO"]
    if os.environ.get("OPENBIODATA_VERBOSE"):
        print("geoname: ", cityname)
    params = {
        "q": city_name,
        "maxRows": 1,
        "username": username
    }
    try:
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        if data.get("geonames"):
            return data["geonames"][0]["countryName"]
    except Exception as e:
        if os.environ.get("OPENBIODATA_VERBOSE"):
            print("GeoNames searchJSON error:", e)
    return None

# Search for country info using alpha-2/3 codes or name
def get_country_from_countryinfo(input_code):
    url = os.environ["URL_COUNTRYJSON"] 
    username = os.environ["USERNAME_GEO"]
    if os.environ.get("OPENBIODATA_VERBOSE"):
        print("countryINFO: ", input_code)
    params = {
        "username": username
    }
    try:
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        if data.get("geonames"):
            input_code = input_code.strip().upper()
            for country in data["geonames"]:
                # Match against country name, country code (alpha-2), iso alpha-3
                if input_code in [
                    country.get("countryName", "").upper(),
                    country.get("countryCode", "").upper(),
                    country.get("isoAlpha3", "").upper()
                ]:
                    return country["countryName"]
    except Exception as e:
        if os.environ.get("OPENBIODATA_VERBOSE"):
            print("GeoNames countryInfoJSON error:", e)
    return None

# Combined smart lookup
def smart_country_lookup(user_input):
    try: 
        raw_input = user_input.strip()
        normalized = re.sub(r"[^a-zA-Z0-9]", "", user_input).upper()  # normalize for codes (no strip spaces!)
        if os.environ.get("OPENBIODATA_VERBOSE"):
            print("raw input for smart country lookup: ",raw_input, ". Normalized country: ", normalized)
        # Special case: if user writes "UK: London" → split and take main country part
        if ":" in raw_input:
            raw_input = raw_input.split(":")[0].strip()  # only take "UK"
        # First try as country code (if 2-3 letters or common abbreviation)
        if len(normalized) <= 3:
          if normalized.upper() in ["UK","U.K","U.K."]:
            country = get_country_from_geonames(normalized.upper())
            if os.environ.get("OPENBIODATA_VERBOSE"):
                print("get_country_from_geonames(normalized.upper()) ", country)  
            if country:
              return country
          else:  
            country = get_country_from_countryinfo(raw_input)
            if os.environ.get("OPENBIODATA_VERBOSE"):
                print("get_country_from_countryinfo(raw_input) ", country)  
            if country:
                return country
        if os.environ.get("OPENBIODATA_VERBOSE"):
            print(raw_input)        
        country = get_country_from_countryinfo(raw_input)  # try full names
        if os.environ.get("OPENBIODATA_VERBOSE"):
            print("get_country_from_countryinfo(raw_input) ", country)
        if country:
            return country
        # Otherwise, treat as city/place
        country = get_country_from_geonames(raw_input)
        if os.environ.get("OPENBIODATA_VERBOSE"):
            print("get_country_from_geonames(raw_input) ", country)
        if country:
            return country
    
        return "Not found"
    except:
        country = model.get_country_from_text(user_input)
        if country.lower() !="unknown":
            return country
        else:
            return "Not found"