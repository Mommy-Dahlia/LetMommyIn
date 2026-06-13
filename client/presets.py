from behavior_manager import DEFAULT_BEHAVIORS, _merge_defaults
import copy

REQUIRED_CHOICE_TAGS = {"masculinity", "femininity", "other gender fuckery", "b*s", "bunny", "puppy"}

def build_mommy_profile(tier: str = "free") -> dict:
    """Returns a profile dict ready to insert into behaviors['profiles']."""
    if tier == "paid":
        return {
            "enabled": {
                "toys_and_teases": True,
                "bunny_bomb": True,
                "wfm": True,
                "rules_and_tasks": True,
                "either_or": True,
                "web_aided_tasks": True,
                "session": True,
                "wallpaper": True,
            },
            "general_frequency": {
                "min_minutes": 10,
                "random_minutes": 10,
            },
            "behavior_weights": {
                "toys_and_teases": 2.0,
                "wfm": 2.0,
                "wallpaper": 0.5,
                "session": 0.5,
            },
            "tag_weights": {},
            "image_popup_opacity": 1.0,
            "image_click_through": False,
        }
    else:
        return {
            "enabled": {
                "toys_and_teases": True,
                "bunny_bomb": True,
                "wfm": True,
                "rules_and_tasks": False,
                "either_or": False,
                "web_aided_tasks": False,
                "session": False,
                "wallpaper": False,
            },
            "general_frequency": {
                "min_minutes": 10,
                "random_minutes": 10,
            },
            "behavior_weights": {},
            "tag_weights": {},
            "image_popup_opacity": 1.0,
            "image_click_through": False,
        }

def build_work_profile(work_start_h: int = 9, work_end_h: int = 17) -> dict:
    """Returns a profile dict for work hours."""
    return {
        "enabled": {
            "toys_and_teases": True,
            "bunny_bomb": False,
            "wfm": False,
            "rules_and_tasks": False,
            "either_or": False,
            "web_aided_tasks": False,
            "session": False,
            "wallpaper": False,
        },
        "general_frequency": {
            "min_minutes": 60,
            "random_minutes": 30,
        },
        "behavior_weights": {},
        "tag_weights": {},
        "image_popup_opacity": 0.3,
        "image_click_through": False,
    }

def build_work_schedule(work_start_h: int = 9, work_end_h: int = 17, main_profile: str = "Onboarding") -> list:
    """Returns a schedule list pairing work and main profiles."""
    return [
        {"start_h": work_start_h, "start_m": 0, "profile": "Work"},
        {"start_h": work_end_h, "start_m": 0, "profile": main_profile},
    ]