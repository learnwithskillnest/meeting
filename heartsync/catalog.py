"""Controlled vocabularies for profile fields and filters.

Deliberately NOT collected: caste, ethnicity, skin tone, income, or other
attributes commonly used for discriminatory filtering.
"""

GENDERS = {
    "woman": "Woman", "man": "Man", "nonbinary": "Non-binary", "transwoman": "Trans woman",
    "transman": "Trans man", "genderqueer": "Genderqueer", "other": "Another identity",
}
RELATIONSHIP_GOALS = {
    "long_term": "Long-term relationship", "marriage": "Marriage", "casual": "Casual dating",
    "friendship": "Friendship", "companionship": "Companionship", "exploring": "Open to exploring",
}
RELATIONSHIP_STATUS = {"single": "Single", "divorced": "Divorced", "separated": "Separated", "widowed": "Widowed"}
EDUCATION = {
    "high_school": "High school", "diploma": "Diploma / vocational", "bachelors": "Bachelor's degree",
    "masters": "Master's degree", "doctorate": "Doctorate", "other": "Other",
}
WORK_STATUS = {
    "employed": "Employed", "self_employed": "Self-employed", "student": "Student",
    "between_jobs": "Between jobs", "retired": "Retired",
}
WORK_SCHEDULE = {"day": "Day shifts", "night": "Night shifts", "flexible": "Flexible", "remote": "Remote"}
INDUSTRIES = {
    "technology": "Technology", "healthcare": "Healthcare", "education": "Education", "finance": "Finance",
    "creative": "Arts & creative", "engineering": "Engineering", "law": "Law", "hospitality": "Hospitality",
    "government": "Government & public sector", "business": "Business & management", "science": "Science & research",
    "media": "Media", "retail": "Retail", "other": "Other",
}
SMOKING = {"never": "Never", "socially": "Socially", "regularly": "Regularly", "quitting": "Trying to quit"}
DRINKING = {"never": "Never", "socially": "Socially", "regularly": "Regularly", "sober": "Sober"}
DIET = {
    "vegetarian": "Vegetarian", "vegan": "Vegan", "eggetarian": "Eggetarian", "non_vegetarian": "Non-vegetarian",
    "jain": "Jain", "pescatarian": "Pescatarian", "flexible": "No restrictions",
}
EXERCISE = {"daily": "Daily", "often": "A few times a week", "sometimes": "Sometimes", "rarely": "Rarely"}
PETS = {"dog": "Have a dog", "cat": "Have a cat", "other_pet": "Have other pets", "want": "Want pets",
        "no_pets": "No pets", "allergic": "Allergic to pets"}
SLEEP = {"early_bird": "Early bird", "night_owl": "Night owl", "flexible": "Flexible"}
RELIGION = {
    "hindu": "Hindu", "muslim": "Muslim", "christian": "Christian", "sikh": "Sikh", "buddhist": "Buddhist",
    "jain": "Jain", "jewish": "Jewish", "parsi": "Parsi / Zoroastrian", "spiritual": "Spiritual",
    "agnostic": "Agnostic", "atheist": "Atheist", "other": "Other",
}
FAMILY_PLANS = {
    "want_kids": "Want children", "dont_want_kids": "Don't want children", "have_kids": "Have children",
    "open": "Open to children", "unsure": "Not sure yet",
}
COMMUNICATION = {
    "texter": "Big texter", "caller": "Phone caller", "video": "Video chatter",
    "in_person": "Better in person", "mix": "A bit of everything",
}
PARTNER_QUALITIES = {
    "kindness": "Kindness", "humour": "Sense of humour", "ambition": "Ambition", "honesty": "Honesty",
    "curiosity": "Curiosity", "loyalty": "Loyalty", "empathy": "Empathy", "adventurous": "Adventurous spirit",
    "family_oriented": "Family-oriented", "independence": "Independence", "patience": "Patience",
    "creativity": "Creativity",
}
LANGUAGES = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "te": "Telugu", "mr": "Marathi", "ta": "Tamil",
    "ur": "Urdu", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "or": "Odia", "pa": "Punjabi",
    "as": "Assamese", "fr": "French", "es": "Spanish", "de": "German", "ar": "Arabic", "zh": "Mandarin",
    "ja": "Japanese",
}
TRAVEL = {
    "mountains": "Mountains", "beaches": "Beaches", "cities": "City breaks", "road_trips": "Road trips",
    "backpacking": "Backpacking", "luxury": "Luxury travel", "pilgrimage": "Spiritual journeys",
    "staycations": "Staycations",
}

INTERESTS = {
    "Active": ["Running", "Yoga", "Cricket", "Football", "Badminton", "Hiking", "Cycling", "Swimming", "Gym", "Dance"],
    "Creative": ["Photography", "Painting", "Writing", "Music production", "Singing", "Design", "Poetry", "Crafts"],
    "Food & drink": ["Cooking", "Baking", "Street food", "Coffee", "Tea", "Fine dining", "Vegan food"],
    "Entertainment": ["Movies", "Bollywood", "Anime", "Stand-up comedy", "Podcasts", "Gaming", "Board games", "Theatre"],
    "Mind": ["Reading", "Philosophy", "Science", "History", "Languages", "Meditation", "Chess", "Astronomy"],
    "Social": ["Volunteering", "Travel", "Festivals", "Concerts", "Startups", "Pets", "Gardening", "Fashion"],
}

# Fields whose visibility the user can control, and their privacy-first defaults.
VISIBILITY_FIELDS = {
    "height_cm": "everyone",
    "education": "everyone",
    "profession": "everyone",
    "industry": "everyone",
    "work_status": "everyone",
    "work_schedule": "matches",
    "relationship_status": "everyone",
    "smoking": "everyone",
    "drinking": "everyone",
    "diet": "everyone",
    "religion": "matches",
    "family_plans": "matches",
    "sleep_habits": "everyone",
    "city": "everyone",
}
VISIBILITY_LEVELS = ("everyone", "matches", "hidden")

SELECT_FIELDS = {
    "relationship_status": RELATIONSHIP_STATUS, "education": EDUCATION, "work_status": WORK_STATUS,
    "work_schedule": WORK_SCHEDULE, "industry": INDUSTRIES, "smoking": SMOKING, "drinking": DRINKING,
    "diet": DIET, "exercise": EXERCISE, "pets": PETS, "sleep_habits": SLEEP, "religion": RELIGION,
    "family_plans": FAMILY_PLANS, "communication_style": COMMUNICATION,
}
MULTI_FIELDS = {
    "interested_in": GENDERS, "languages": LANGUAGES, "relationship_goals": RELATIONSHIP_GOALS,
    "partner_qualities": PARTNER_QUALITIES, "travel_interests": TRAVEL,
}

# Approximate city centroids so distance works without collecting device GPS.
CITIES = {
    ("IN", "Uttar Pradesh", "Lucknow"): (26.85, 80.95),
    ("IN", "Delhi", "New Delhi"): (28.61, 77.21),
    ("IN", "Maharashtra", "Mumbai"): (19.08, 72.88),
    ("IN", "Maharashtra", "Pune"): (18.52, 73.86),
    ("IN", "Karnataka", "Bengaluru"): (12.97, 77.59),
    ("IN", "Telangana", "Hyderabad"): (17.39, 78.49),
    ("IN", "Tamil Nadu", "Chennai"): (13.08, 80.27),
    ("IN", "West Bengal", "Kolkata"): (22.57, 88.36),
    ("IN", "Gujarat", "Ahmedabad"): (23.02, 72.57),
    ("IN", "Rajasthan", "Jaipur"): (26.91, 75.79),
    ("IN", "Uttar Pradesh", "Kanpur"): (26.45, 80.33),
    ("IN", "Uttar Pradesh", "Noida"): (28.54, 77.39),
    ("IN", "Haryana", "Gurugram"): (28.46, 77.03),
    ("IN", "Punjab", "Chandigarh"): (30.73, 76.78),
    ("IN", "Kerala", "Kochi"): (9.93, 76.27),
    ("IN", "Madhya Pradesh", "Indore"): (22.72, 75.86),
    ("IN", "Madhya Pradesh", "Bhopal"): (23.26, 77.41),
    ("IN", "Bihar", "Patna"): (25.59, 85.14),
    ("IN", "Goa", "Panaji"): (15.49, 73.83),
    ("IN", "Uttarakhand", "Dehradun"): (30.32, 78.03),
}
COUNTRIES = {"IN": "India"}


def city_coords(country: str | None, region: str | None, city: str | None):
    return CITIES.get((country or "", region or "", city or ""))


def regions(country: str = "IN") -> list[str]:
    return sorted({r for (c, r, _) in CITIES if c == country})


def cities(country: str = "IN") -> list[dict]:
    return [{"country": c, "region": r, "city": ci} for (c, r, ci) in sorted(CITIES) if c == country]
