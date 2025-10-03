# views.py
import os
import re
import json
import base64
import requests
from typing import Dict, Tuple

from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt

# ---------- ENV ----------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ELEVEN_KEY     = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_VOICE   = os.getenv("ELEVENLABS_VOICE_ID", "Bella")

# OpenAI client (optional if key missing; we’ll guard calls)
try:
    from openai import OpenAI
    openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    openai_client = None

# ---------- BRAND CONTEXT ----------
BRAND_FACTS = """
Company: Selerna Group.
Positioning: Bridges AI confusion to business clarity with people-first strategy and engineered execution.
Flagship: AI Strategy Blueprint (Discovery Scan → Opportunity Architecture → Transformation Pathway).
Executive Service: Fractional Chief Transformation Architect (CTA) — aligns AI, tech, data, operations, and people across the C-suite.
Founder: Steve Sellars — 30+ years; human-centered innovation across telecom, healthcare, maritime, and automation; certified in AI Strategy, Cybersecurity, Data Governance, and Personal Change Management.
Tone: warm, clear, outcome-first; no hype, no chaos; 1–2 sentences max with a light follow-up question.
Primary CTAs: “Book Your Discovery Call”, “Explore Our Blueprint”.
Differentiators: proprietary frameworks, execution-first roadmaps, change management, cross-functional alignment, empowered teams, measurable outcomes.
Socials present: Instagram, X/Twitter, LinkedIn. Contact: 407-955-9455, contact@selernagroup.com
Audience: growth-stage and mid-market leaders (CEOs, Founders, Operators).
Availability note: limited 1:1 engagements to protect quality.
"""

SYSTEM_PROMPT = f"""
You are Steve, Selerna Group’s warm, conversational AI guide.
• Keep answers to 1–2 sentences.
• Be people-first, outcome-focused, calm, and specific.
• End most replies with a light follow-up question (e.g., “Would you like a quick example?”).
• Offer next steps naturally: “Book Your Discovery Call” or “Explore Our Blueprint” when helpful.
• Never make guarantees, pricing promises, or regulated claims.
• Do not invent client names or case studies.
• If unsure, ask a clarifying question or invite a call.
Relevant facts:
{BRAND_FACTS}
"""

# ---------- HELPER UTILS ----------
def _buf_to_b64(b: bytes) -> str:
    return base64.b64encode(b).decode("utf-8")

def _naive_lipsync(text: str, total_ms: int = 1600) -> Dict:
    # Simple heuristic lipsync; replace with visemes when you move to WS streaming
    words = [w for w in text.split() if w.strip()]
    if not words:
        return {"mouthCues": []}

    def pick_viseme(w: str) -> str:
        w = w.lower()
        if any(v in w for v in "ao"): return "D"  # AA
        if any(v in w for v in "ei"): return "C"  # I
        if "u" in w: return "F"                   # U
        if "o" in w: return "E"                   # O
        return "A"                                # PP

    slice_ms = max(80, total_ms // max(1, len(words)))
    cues = []
    for i, w in enumerate(words):
        start = (i * slice_ms) / 1000
        end = ((i + 1) * slice_ms) / 1000
        cues.append({"start": start, "end": end, "value": pick_viseme(w)})
    return {"mouthCues": cues}

def _choose_face(reply_lower: str) -> str:
    if any(k in reply_lower for k in ["great", "glad", "awesome", "happy", "nice", "thanks", "sounds good"]):
        return "smile"
    if any(k in reply_lower for k in ["sorry", "unfortunately", "concern"]):
        return "concerned"
    return "default"

def _quick_replies(keys):
    return keys or []

# ---------- INTENT ROUTER ----------
# Order matters (most specific first)
ROUTES: Tuple[Tuple[str, str], ...] = (
    ("company_name", r"\b(what('?s)?\s*(your|the)\s*company\s*name|who\s*are\s*you|selerna\s*group)\b"),
    ("established", r"\b(when|what\s*year)\s*(were\s*you\s*)?(founded|established|start(ed)?)\b"),
    ("blueprint", r"\b(blueprint|discovery\s*scan|opportunity\s*architecture|transformation\s*pathway|stage\s*[123])\b"),
    ("cta", r"\b(CTA|chief\s*transformation\s*architect|fractional\s*chief|transformation\s*architect)\b"),
    ("services", r"\b(services?|offer|what\s*do\s*you\s*do|how\s*you\s*help|capabilit(y|ies))\b"),
    ("why_us", r"\b(why\s*(choose|selerna)|what\s*makes.*different|differentiator|value)\b"),
    ("about_steve", r"\b(steve\s+sellars|who\s*is\s*steve|founder)\b"),
    ("testimonials", r"\b(testimonial|what\s*clients\s*say|reviews?)\b"),
    ("client_journey", r"\b(client\s*journey|week\s*1|month\s*3|month\s*6|\bjourney\b)\b"),
    ("industries", r"\b(healthcare|telecom|maritime|automation|industry|industries)\b"),
    ("contact", r"\b(contact|email|phone|reach|call\s+you)\b"),
    ("book", r"\b(book|schedule|discovery\s*call|consult|talk\s*to\s*(you|steve)|meet)\b"),
    ("socials", r"\b(instagram|linkedin|twitter|x)\b"),
    ("availability", r"\b(availability|limited|waitlist|slots?)\b"),
    ("pricing", r"\b(price|pricing|cost|rates?)\b"),
)
# Pre-compile for speed
ROUTE_REGEX = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in ROUTES]

CANNED: Dict[str, Tuple[str, list]] = {
    "company_name": (
        "We’re Selerna Group—helping leaders turn AI confusion into clear, practical execution; what outcome are you hoping AI can drive first?",
        ["Explore Our Blueprint", "Book Your Discovery Call"],
    ),
    "established": (
        "We were founded in 2023 to bridge strategy and adoption with people-first execution; are you exploring AI for efficiency, growth, or both?",
        ["Explore Our Blueprint", "Book Your Discovery Call"],
    ),
    "blueprint": (
        "Our AI Strategy Blueprint moves from Discovery Scan to Opportunity Architecture to a clear Transformation Pathway—would a quick example of those stages help?",
        ["Book Your Discovery Call", "Explore Our Blueprint"],
    ),
    "cta": (
        "The Fractional Chief Transformation Architect aligns AI, data, operations, and people across the C-suite to turn strategy into measurable execution—should I outline how this works alongside your current leaders?",
        ["Book Your Discovery Call"],
    ),
    "services": (
        "We integrate AI strategy with workflow design, data governance, and change management so teams adopt faster and value shows up sooner—where do you feel the biggest bottleneck today?",
        ["Explore Our Blueprint", "Book Your Discovery Call"],
    ),
    "why_us": (
        "We pair proprietary frameworks with execution-first roadmaps and people-centered change so transformation is practical and measurable—would you like the two or three moves we’d assess first?",
        ["Explore Our Blueprint"],
    ),
    "about_steve": (
        "Steve Sellars created the CTA role and the Blueprint; with 30+ years across telecom, healthcare, maritime, and automation he turns complexity into human-centered results—want a 1-minute background or jump to next steps?",
        ["Book Your Discovery Call"],
    ),
    "testimonials": (
        "Leaders highlight our clarity over hype and the shift from slides to execution—shall I share common outcomes teams report in the first 60–90 days?",
        ["Explore Our Blueprint"],
    ),
    "client_journey": (
        "Week 1 alignment, Week 4 working blueprint, Month 3 workflow gains, Month 6+ operating advantage—do you want to map what Weeks 1–4 could look like for you?",
        ["Book Your Discovery Call"],
    ),
    "industries": (
        "We bring deep experience across healthcare, telecom, maritime, and automation and apply proven patterns to growth-stage and mid-market teams—what industry are you in so I can tailor examples?",
        ["Book Your Discovery Call"],
    ),
    "contact": (
        "You can reach us at 407-955-9455 or contact@selernagroup.com—would you prefer I set up a quick Discovery Call?",
        ["Book Your Discovery Call"],
    ),
    "book": (
        "Great—let’s schedule a Discovery Call and zero in on your highest-value moves; does this week or next work better?",
        ["Book Your Discovery Call"],
    ),
    "socials": (
        "We share practical insights on Instagram, LinkedIn, and X—would links be helpful or should we focus on your use case?",
        ["Explore Our Blueprint"],
    ),
    "availability": (
        "We keep 1:1 engagements limited to protect quality; if your timing’s tight we can prioritize your Discovery Call—what window are you targeting?",
        ["Book Your Discovery Call"],
    ),
    "pricing": (
        "We tailor scope to outcomes and team readiness—shall we start with a short assessment to size effort before we talk numbers?",
        ["Book Your Discovery Call"],
    ),
    "fallback": (
        "I can help with the Blueprint, the CTA, or mapping quick wins—what’s the challenge you’re trying to solve first?",
        ["Explore Our Blueprint", "Book Your Discovery Call"],
    ),
}

def route_intent(text: str) -> str:
    t = text.lower()
    for name, regex in ROUTE_REGEX:
        if regex.search(t):
            return name
    return "fallback"

# ---------- MAIN CHAT ----------
@csrf_exempt
def chat(request):
    if request.method != "POST":
        return JsonResponse({"detail": "POST only"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8"))
        user_text = (body.get("message") or "").strip()
        if not user_text:
            return JsonResponse({"messages": []})

        # 1) Route to brand microcopy
        intent = route_intent(user_text)
        canned_text, canned_buttons = CANNED.get(intent, CANNED["fallback"])
        reply = canned_text
        reply_source = "canned"
        quick_replies = _quick_replies(canned_buttons)

        # 2) Try OpenAI (prefer model when available)
        use_model = True if openai_client else False  # always try model when key present
        if use_model:
            try:
                completion = openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text},
                    ],
                    temperature=0.5,
                    max_tokens=220,  # crisp but not tiny
                )
                model_reply = (completion.choices[0].message.content or "").strip()
                if model_reply:
                    # soft-cap instead of rejecting long text
                    reply = model_reply[:600].strip()
                    reply_source = "model"
            except Exception as e:
                # keep canned; log for debugging
                print("OpenAI error:", repr(e))

        # 3) ElevenLabs TTS (optional)
        audio_b64 = None
        if ELEVEN_KEY and ELEVEN_VOICE and reply:
            try:
                tts_url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE}"
                hdrs = {"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"}
                payload = {
                    "text": reply,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.85,
                        "style": 0.22,
                        "use_speaker_boost": True,
                    },
                    "output_format": "mp3_44100_128",
                }
                r = requests.post(tts_url, headers=hdrs, json=payload, timeout=60)
                if r.status_code == 200:
                    audio_b64 = _buf_to_b64(r.content)
                else:
                    try:
                        print("ElevenLabs error:", r.status_code, r.json())
                    except Exception:
                        print("ElevenLabs error:", r.status_code, r.text)
            except Exception as tts_e:
                print("TTS exception:", repr(tts_e))

        # 4) Avatar packaging
        lipsync = _naive_lipsync(reply, max(1200, len(reply) * 45))
        facial = _choose_face(reply.lower())
        animation = "Idle"

        # Optional debug:
        # print("DEBUG reply_source:", reply_source)
        # print("DEBUG intent:", intent)

        return JsonResponse({
            "messages": [{
                "text": reply,
                "audio": audio_b64,
                "lipsync": lipsync,
                "facialExpression": facial,
                "animation": animation,
                "quick_replies": quick_replies,
                "intent": intent,
                "source": reply_source,  # <-- confirm in DevTools → Network → /chat → Response
            }]
        })

    except Exception as e:
        print("chat error:", repr(e))
        return JsonResponse({
            "messages": [{
                "text": "I hit a snag—let’s book a quick Discovery Call and get you moving.",
                "audio": None,
                "lipsync": {"mouthCues": []},
                "facialExpression": "default",
                "animation": "Idle",
                "quick_replies": ["Book Your Discovery Call"],
                "intent": "error",
                "source": "error",
            }]
        }, status=500)

def health(request):
    return HttpResponse("ok", content_type="text/plain")
