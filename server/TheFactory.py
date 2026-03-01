import csv
import random
import re
import json

# --- HARD CODE THESE IF YOU WANT ---
CSV_PATH = "images.csv"
TXT_PATH = "script.txt"
OUTPUT_PATH = "plan.json"

# Precompiled pattern to detect #PIC lines with optional tags
PIC_PATTERN = re.compile(r"#PIC\b(?:\s*-\s*(.*))?", re.IGNORECASE)

# New: detect #INV lines
INV_PATTERN = re.compile(r"#INV\b", re.IGNORECASE)

#detect #THR lines
THR_PATTERN = re.compile(r"#THR\b", re.IGNORECASE)

PNS_POOL = [
    "cuckie",
    "sweetheart",
    "pet",
    "toy",
    "cuck"
]

# New: invite link
DISCORD_INVITE_URL = "https://discord.gg/jgyWNMh3Bu"
THRONE_URL = "https://throne.com/obeymommydahlia"

AUDIO_PATTERN = re.compile(r"#AUDIO\b(?:\s*-\s*(.*))?", re.IGNORECASE)
AUDIO_STOP_PATTERN = re.compile(r"#AUDIOSTOP\b", re.IGNORECASE)

SUB_PATTERN = re.compile(r"#SUB\b(?:\s*-\s*(.*))?", re.IGNORECASE)

WFM_PATTERN = re.compile(
    r"#WFM\b\s*-\s*(.*?)(?:\s*,\s*(\d+))?$",
    re.IGNORECASE,
)

GIF_PATTERN = re.compile(r"#GIF\b(?:\s*-\s*(.*))?", re.IGNORECASE)
GIF_STOP_PATTERN = re.compile(r"#GIFSTOP\b", re.IGNORECASE)

AUTO_DEFAULT_GIF = True
DEFAULT_GIF_URL = "https://pub-6dd573008dee4009bea8855056470713.r2.dev/OMD/mommy1.gif"

AUTO_DEFAULT_AUDIO = True
DEFAULT_AUDIO_URL = "https://pub-6dd573008dee4009bea8855056470713.r2.dev/OMD/myNoise_BinauralBeats_63000063000000000000_0_5%20(1).mp3"

DEFAULT_GIF_OVERLAY_OPACITY = 0.4
DEFAULT_GIF_OVERLAY_SCREEN = None

DEFAULT_AUDIO_VOLUME = 0.8
DEFAULT_AUDIO_LOOP = True

SUBLIMINAL_SETS = {
    # tags -> messages list (keep these SFW; you can expand freely)
    "premelt": ["Don't think.", "Be passive.", "Sink.", "Absorb.", "Mommy's here~", "Be docile.", "Empty", "Relaxed", "Ready"],
    "lmi": ["Let Mommy In.", "Let Mommy In", "Let Mommy In~", "LMI", "Mommy's Always Watching"],
    "horny": ["Horny", "Needy", "Desperate", "So horny", "Hornier", "Be Horny", "Stay Horny", "Pleasure", "Lavender Love"],
    "melted": ["Open", "Obedient", "Willing", "Absorbing", "Accepting", "Mommy's", "Lost", "Helpless"]
}

PNS_PATTERN = re.compile(r"#PNS", re.IGNORECASE)

def replace_pns_per_occurrence(lines: list[str], pool: list[str]) -> list[str]:
    """
    Replace each occurrence of #PNS with a random pick from pool (with replacement).
    Each #PNS draws independently.
    """
    if not pool:
        return lines

    def repl(_match):
        return random.choice(pool)

    return [PNS_PATTERN.sub(repl, line) for line in lines]

DEFAULT_PACING_S = 8.0

def ensure_timer_s_everywhere(steps: list[dict]) -> None:
    for s in steps:
        if s.get("timer_s") is None:
            if s.get("type") in ("audio_play", "subliminal_start"):
                s["timer_s"] = 0.0
            else:
                s["timer_s"] = DEFAULT_PACING_S

def parse_sub_tags(tag_part: str | None) -> list[str]:
    """
    Accept:
      "#SUB - tag1, tag2"
      "#SUB - tag1 tag2"  (optional)
    Return normalized tags.
    """
    if not tag_part:
        return []

    # Prefer comma-separated, but tolerate whitespace-separated
    raw = tag_part.replace(",", " ").split()
    return [normalize_tag(t) for t in raw if t.strip()]


def build_subliminal_messages(tags: list[str]) -> list[str]:
    """
    Union of message sets across tags, de-duped but stable order.
    """
    msgs: list[str] = []
    seen = set()

    for tag in tags:
        tag_msgs = SUBLIMINAL_SETS.get(tag)
        if not tag_msgs:
            raise ValueError(f"Unknown subliminal tag: {tag!r}")

        for m in tag_msgs:
            if m not in seen:
                seen.add(m)
                msgs.append(m)

    if not msgs:
        raise ValueError("No subliminal messages resolved from tags")

    return msgs

def ensure_default_audio(steps: list[dict]) -> None:
    """
    If AUTO_DEFAULT_AUDIO is enabled and there is no audio_play step,
    inject one at the beginning with no duration yet.
    """
    if not AUTO_DEFAULT_AUDIO:
        return

    has_audio = any(s.get("type") == "audio_play" for s in steps)
    if has_audio:
        return

    steps.insert(0, {
        "type": "audio_play",
        "url": DEFAULT_AUDIO_URL,
        "volume": DEFAULT_AUDIO_VOLUME,
        "loop": DEFAULT_AUDIO_LOOP,
        "timer_s": 0,  # don't consume pacing time
        # duration_s gets filled in later based on session length
    })

def ensure_default_gif_overlay(steps: list[dict]) -> None:
    """
    If AUTO_DEFAULT_GIF is enabled and there is no gif_overlay step,
    inject a default gif_overlay at the beginning.

    End-of-session stop is handled by apply_effect_scoping(), which appends
    gif_overlay_stop if any overlay was started.
    """
    if not AUTO_DEFAULT_GIF:
        return

    has_gif = any(s.get("type") in ("gif_overlay", "gif_overlay_stop") for s in steps)
    if has_gif:
        return

    url = (DEFAULT_GIF_URL or "").strip()
    if not url:
        raise ValueError("AUTO_DEFAULT_GIF is True but DEFAULT_GIF_URL is empty")

    steps.insert(0, {
        "type": "gif_overlay",
        "url": url,
        "opacity": DEFAULT_GIF_OVERLAY_OPACITY,
        "screen": DEFAULT_GIF_OVERLAY_SCREEN,
        "timer_s": 0,  # dispatch immediately; no pacing consumed
    })

def normalize_tag(tag: str) -> str:
    """Normalize tags for consistent matching."""
    return tag.strip().lower()


def load_images(csv_path):
    """
    Load CSV into:
    - images: list of dicts {url: str, tags: set()}
    Tags are normalized to lowercase.
    """
    images = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        # All columns except the URL column (case-insensitive)
        tag_columns = [
            col for col in reader.fieldnames
            if col and col.strip().lower() != "url"
        ]

        for row in reader:
            url = row["url"].strip()
            if not url:
                continue

            tags = set()
            for col in tag_columns:
                value = (row.get(col) or "").strip()
                # You can broaden this if your CSV has "true", "yes", etc.
                if value == "X":
                    tags.add(normalize_tag(col))

            images.append({"url": url, "tags": tags})

    return images


def load_script(txt_path):
    """Load all lines from the .txt script file."""
    with open(txt_path, "r", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f]


def extract_pic_tags(line):
    """
    Given: "#PIC - outside, blue"
    Return: {"outside", "blue"} (normalized to lowercase)

    Handles:
      - "#PIC"
      - "#pic - outside, blue"
      - with or without the hyphen
    """
    match = PIC_PATTERN.match(line.strip())
    if not match:
        return set()

    tag_part = match.group(1)
    if not tag_part:
        return set()

    tags = {
        normalize_tag(t)
        for t in tag_part.split(",")
        if t.strip()
    }
    return tags


def extract_delays(script_lines):
    """
    For each line, detect trailing '#N' and:
      - remove it from the line
      - store delay = N * 1000

    Returns:
      cleaned_lines: list[str]   (with '#N' removed)
      delays:     list[int]   (same length, per-line delay)
    """
    cleaned_lines = []
    delays = []

    delay_pattern = re.compile(r"(.*?)(?:\s*#(\d+))\s*$")

    for line in script_lines:
        m = delay_pattern.match(line)
        if m:
            text = m.group(1).rstrip()
            delay = int(m.group(2))
        else:
            text = line
            delay = None

        cleaned_lines.append(text)
        delays.append(delay)

    return cleaned_lines, delays


def assign_images(script_lines, images):
    """
    Replace #PIC lines with selected image URLs.
    Uses tag specificity priority — more tags first.
    Ensures no image repeats.

    Note: #INV lines are not changed here.
    """

    # Store results but keep structure
    processed = [None] * len(script_lines)

    # Gather PIC requests
    pic_requests = []
    for idx, line in enumerate(script_lines):
        if PIC_PATTERN.match(line.strip()):
            tags = extract_pic_tags(line)
            pic_requests.append((idx, tags))

    # Sort PICs: more tags first (higher specificity)
    pic_requests.sort(key=lambda x: len(x[1]), reverse=True)

    unused_images = images.copy()

    def eligible_images(request_tags):
        """Return all unused images matching ALL requested tags."""
        return [
            img for img in unused_images
            if request_tags.issubset(img["tags"])
        ]

    for idx, tags in pic_requests:
        if tags:
            candidates = eligible_images(tags)
        else:
            # If no tags requested, any unused image is fine
            candidates = unused_images

        if not candidates:
            raise ValueError(
                f"No available images left that match tags {tags}. "
                "You may need more or differently tagged images in the CSV."
            )

        img = random.choice(candidates)

        processed[idx] = img["url"]  # assign URL
        # Remove chosen image from unused set
        unused_images = [i for i in unused_images if i["url"] != img["url"]]

    # Fill non-PIC lines (including #INV and normal text)
    for idx, line in enumerate(script_lines):
        if processed[idx] is None:
            processed[idx] = line

    return processed


def wrap_output(lines, delays):
    steps = []

    for idx, line in enumerate(lines):
        timer = delays[idx] if idx < len(delays) else None
        stripped = line.strip()

        step = None

        if INV_PATTERN.fullmatch(stripped):
            step = {"type": "open_url", "body": DISCORD_INVITE_URL}
        elif THR_PATTERN.fullmatch(stripped):
            step = {"type": "open_url", "body": THRONE_URL}
        elif AUDIO_STOP_PATTERN.fullmatch(stripped):
            step = {"type": "audio_stop"}
            # don’t pause the session when stopping audio
            step["timer_s"] = 0
        elif AUDIO_PATTERN.fullmatch(stripped):
            url = (AUDIO_PATTERN.fullmatch(stripped).group(1) or "").strip() or DEFAULT_AUDIO_URL
            step = {"type": "audio_play", "url": url, "loop": True, "volume": 0.8}
            # start immediately; don’t burn 8 seconds of session pacing
            step["timer_s"] = 0
        elif SUB_PATTERN.fullmatch(stripped):
            tags = parse_sub_tags(SUB_PATTERN.fullmatch(stripped).group(1))
            msgs = build_subliminal_messages(tags)
            if not msgs:
                raise ValueError(f"Unknown subliminal tag: {tags}")
            step = {
                "type": "subliminal_start",
                "messages": msgs,
                "interval_ms": 2000,
                "flash_ms": 40,
                "font_pt": 40,
            }
            step["timer_s"] = 0
        elif stripped.startswith("http://") or stripped.startswith("https://"):
            step = {"type": "image_popup", "body": stripped}
            
        elif WFM_PATTERN.fullmatch(stripped):
            m = WFM_PATTERN.fullmatch(stripped)
            text = (m.group(1) or "").strip()
            reps = m.group(2)

            if not text:
                raise ValueError("WFM directive requires text")

            try:
                reps_val = int(reps) if reps is not None else 3
            except Exception:
                raise ValueError(f"Invalid WFM reps: {reps!r}")

            step = {
                "type": "write_for_me",
                "text": text,
                "reps": reps_val,
                "timer_s": 0,  # dispatch immediately; session pauses
            }
        elif GIF_STOP_PATTERN.fullmatch(stripped):
            step = {"type": "gif_overlay_stop", "timer_s": 0}

        elif GIF_PATTERN.fullmatch(stripped):
            # Format: "#GIF - <url>"  (keep MVP simple; more params later)
            m = GIF_PATTERN.fullmatch(stripped)
            url = (m.group(1) or "").strip() or DEFAULT_GIF_URL

            step = {
                "type": "gif_overlay",
                "url": url,
                "opacity": DEFAULT_GIF_OVERLAY_OPACITY,
                "screen": DEFAULT_GIF_OVERLAY_SCREEN,
                "timer_s": 0,
            }
        
        else:
            # message: keep it simple; title can be added later if you want
            step = {"type": "show_message", "title": "Let Mommy In", "body": line}

        if timer is not None:
            step["timer_s"] = int(timer)

        steps.append(step)

    return steps

def effective_step_timer_s(step: dict) -> float:
    # The client defaults missing timer_s to 8s pacing
    t = step.get("timer_s")
    if t is None:
        return float(DEFAULT_PACING_S)
    try:
        return float(t)
    except Exception:
        return 0.0

def apply_effect_scoping(steps: list[dict]) -> list[dict]:
    out: list[dict] = []

    saw_audio = False
    saw_sub = False
    saw_gif = False

    sub_active = False
    gif_active = False

    for step in steps:
        st = step.get("type")

        if st == "audio_play":
            saw_audio = True

        if st == "subliminal_start":
            saw_sub = True
            if sub_active:
                out.append({"type": "subliminal_stop", "timer_s": 0})
            sub_active = True

        if st == "gif_overlay":
            saw_gif = True
            if gif_active:
                out.append({"type": "gif_overlay_stop", "timer_s": 0})
            gif_active = True

        out.append(step)

        if st == "subliminal_stop":
            sub_active = False
        if st == "gif_overlay_stop":
            gif_active = False

    # end-of-session cleanup
    if saw_sub and (not out or out[-1].get("type") != "subliminal_stop"):
        out.append({"type": "subliminal_stop", "timer_s": 0})

    if saw_gif and (not out or out[-1].get("type") != "gif_overlay_stop"):
        out.append({"type": "gif_overlay_stop", "timer_s": 0})

    if saw_audio and (not out or out[-1].get("type") != "audio_stop"):
        out.append({"type": "audio_stop", "timer_s": 0})

    return out

def write_plan_json(steps, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(steps, f, indent=2)

def main():
    images = load_images(CSV_PATH)
    raw_script_lines = load_script(TXT_PATH)

    # 1) Extract per-line delays from trailing #N, clean the lines
    script_lines, delays = extract_delays(raw_script_lines)

    # 2) Replace #PIC lines with URLs, honoring tags
    processed = assign_images(script_lines, images)
    
    #processed = replace_pns_per_occurrence(processed, PNS_POOL)
    
    # 3) Wrap in JS commands, using the previously extracted delays
    wrapped = wrap_output(processed, delays)
    ensure_timer_s_everywhere(wrapped)
    ensure_default_audio(wrapped)
    ensure_default_gif_overlay(wrapped)
    wrapped = apply_effect_scoping(wrapped)
    write_plan_json(wrapped, OUTPUT_PATH)
    print(f"Finished! Output written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
