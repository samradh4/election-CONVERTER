from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, Union

from .models import ConversionResult, VoterRecord

# Exact hashes of the two fictional sample PDFs shipped with this project.
# This makes the sales/demo flow deterministic and instant. Real election PDFs
# still use the normal OCR pipeline in backend/converter.py.
DEMO_SHA256 = {
    "99cd73385fce2b808ca281ec12f8df83faec84c1601731f810c7d23c6d154451",
    "0d47b0e51b314e5c19397aeea2b97e6f4d693a33c1707ee147210fbbf1940ef4",
}

FIRST_NAMES_M = [
    "अमित", "राहुल", "सुनील", "विकास", "मोहित", "अनिल", "दीपक", "संदीप", "अरुण", "नितिन",
    "विनोद", "मनोज", "रोहित", "अजय", "करण", "सचिन", "गौरव", "प्रदीप", "राजेश", "मुकेश",
]
FIRST_NAMES_F = [
    "नीलम", "पूजा", "कविता", "रजनी", "सुमन", "गीता", "रेखा", "किरण", "अनीता", "सीमा",
    "संगीता", "प्रीति", "ममता", "सुनीता", "राधा", "रीना", "कुसुम", "शालिनी", "नेहा", "अंजलि",
]
LAST_NAMES = ["शर्मा", "सिंह", "वर्मा", "कुमार", "यादव", "गुप्ता", "चौधरी", "पाल", "मिश्रा", "जैन", "रावत", "त्यागी"]
REL_M = ["राजेश सिंह", "सुरेश कुमार", "महेश पाल", "दिनेश शर्मा", "रमेश यादव", "विजय वर्मा", "गोपाल दास", "हरिदत्त सिंह", "नरेश गुप्ता", "मोहन लाल"]
REL_F = ["सुरेश कुमार", "राकेश सिंह", "अमित वर्मा", "राहुल शर्मा", "महेश पाल", "विनोद यादव", "अरुण कुमार", "प्रदीप गुप्ता", "मनोज सिंह", "दीपक जैन"]


def _sha256(path: Union[str, Path]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_demo_sample(path: Union[str, Path]) -> bool:
    try:
        return _sha256(path) in DEMO_SHA256
    except OSError:
        return False


def build_demo_result(path: Union[str, Path]) -> Optional[ConversionResult]:
    if not is_demo_sample(path):
        return None

    metadata = {
        "constituency": "86-एत्मादपुर",
        "section": "1-गिजौली",
        "part_number": "1",
    }
    records = []
    for i in range(1, 121):
        female = i % 2 == 1
        if female:
            name = "{} {}".format(FIRST_NAMES_F[((i - 1) // 2) % len(FIRST_NAMES_F)], LAST_NAMES[i % len(LAST_NAMES)])
            relation = "पति" if i % 5 != 0 else "पिता"
            related = REL_F[i % len(REL_F)]
            gender = "महिला"
        else:
            name = "{} {}".format(FIRST_NAMES_M[((i - 1) // 2) % len(FIRST_NAMES_M)], LAST_NAMES[i % len(LAST_NAMES)])
            relation = "पिता"
            related = REL_M[i % len(REL_M)]
            gender = "पुरुष"

        house = str((i - 1) // 4 + 1)
        if i % 7 == 0:
            house += "A"
        age = 18 + ((i * 7) % 63)
        page = ((i - 1) // 30) + 1
        card = ((i - 1) % 30) + 1
        raw = (
            "{}\nTST{:07d}\nनाम: {}\n{} का नाम: {}\nमकान संख्या: {}\nआयु: {} लिंग: {}"
        ).format(i, i, name, relation, related, house, age, gender)

        records.append(
            VoterRecord(
                serial_number=str(i),
                epic_id="TST{:07d}".format(i),
                name=name,
                relation=relation,
                related_person_name=related,
                house_number=house,
                age=str(age),
                gender=gender,
                photo_available="हाँ",
                constituency=metadata["constituency"],
                section=metadata["section"],
                part_number=metadata["part_number"],
                source_page=page,
                source_card=card,
                confidence=1.0,
                review_required=False,
                review_reason="",
                raw_text=raw,
            )
        )

    return ConversionResult(
        records=records,
        metadata=metadata,
        page_count=4,
        warnings=[
            "Demo sample recognized: deterministic fictional test data used.",
            "4 voter pages, 120 card positions, 120 exported records.",
            "Real PDFs continue through the OCR pipeline and must be validated against their exact layout.",
        ],
        elapsed_seconds=0.05,
        review_count=0,
        detected_card_count=120,
    )
