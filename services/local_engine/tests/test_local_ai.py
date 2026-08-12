from __future__ import annotations

import uuid

import pymupdf

from tecs_engine import local_ai
from tecs_engine.local_ai import (
    _fixture_from_ai,
    _legend_regions,
    _prompt,
    _validate_against_legend_rows,
)
from tecs_engine.models import FixtureRequirement


def test_verified_rows_replace_malformed_ai_duplicates(tmp_path, monkeypatch) -> None:
    pdf_path = tmp_path / "drawing.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=400)
    page.insert_text((50, 50), "LIGHTING LEGEND")
    document.save(pdf_path)
    document.close()

    verified = FixtureRequirement(
        id=str(uuid.uuid4()),
        symbol="SL1",
        description="50W, 7200 lm IP66 street light",
        quantity=71,
        drawing_quantity=71,
        fixture_type="street light",
        wattage=50,
        lumens=7200,
        ip_rating=66,
        source_file=pdf_path.name,
    )
    monkeypatch.setattr(local_ai.runtime, "wait_until_ready", lambda: True)
    monkeypatch.setattr(
        local_ai.runtime,
        "analyse",
        lambda *args, **kwargs: {
            "fixtures": [
                {
                    "symbol": "50W; 7200L",
                    "description": "incorrect duplicate street-light row",
                    "fixture_type": "street light",
                }
            ]
        },
    )
    monkeypatch.setattr(
        local_ai,
        "verified_schedule_fixtures",
        lambda *args, **kwargs: [verified],
    )

    result = local_ai.extract_pdf_with_local_ai(pdf_path)

    assert [fixture.symbol for fixture in result.fixtures] == ["SL1"]
    assert result.fixtures[0].wattage == 50


def test_local_runtime_repairs_malformed_json_once(monkeypatch) -> None:
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"fixtures":[{"symbol":"F1" "description":"32W light"}]}'
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"fixtures":[{"symbol":"F1","description":"32W light"}]}'
                        }
                    }
                ]
            },
        ]
    )
    requests: list[dict] = []

    def fake_http(url, payload=None, **kwargs):
        requests.append(payload)
        return next(responses)

    vision_runtime = local_ai.LocalVisionRuntime()
    monkeypatch.setattr(vision_runtime, "wait_until_ready", lambda: True)
    monkeypatch.setattr(local_ai, "_http_json", fake_http)

    result = vision_runtime.analyse("extract fixtures", [])

    assert result["fixtures"][0]["symbol"] == "F1"
    assert len(requests) == 2
    assert "Repair the supplied JSON" in requests[1]["messages"][0]["content"]


def test_local_ai_fixture_preserves_complete_requirements() -> None:
    fixture = _fixture_from_ai(
        {
            "symbol": "a1",
            "description": "Suspended LED luminaire with coated steel body and opal diffuser",
            "quantity": 12,
            "drawing_quantity": 6,
            "units_per_assembly": 2,
            "fixture_type": "linear luminaire",
            "mounting": "suspended",
            "mounting_height_mm": 2800,
            "wattage": 53,
            "lumens": 6400,
            "cct": 4000,
            "cri": 90,
            "ip_rating": 20,
            "dimensions": "1449 x 60 mm",
            "construction": "coated steel body",
            "optical_details": "opal acrylic diffuser",
            "controls": ["DALI"],
            "confidence": 0.94,
        },
        "drawing.pdf",
        2,
    )

    assert fixture is not None
    assert fixture.symbol == "A1"
    assert fixture.quantity == 12
    assert fixture.drawing_quantity == 6
    assert fixture.units_per_assembly == 2
    assert fixture.mounting_height_mm == 2800
    assert fixture.construction == "coated steel body"
    assert fixture.optical_details == "opal acrylic diffuser"
    assert fixture.controls == ["DALI"]
    assert fixture.source_page == 2
    assert fixture.status == "review"


def test_local_ai_does_not_invent_missing_quantity() -> None:
    fixture = _fixture_from_ai(
        {
            "symbol": "L2",
            "description": "Recessed downlight, wattage not stated",
            "quantity": None,
            "fixture_type": "downlight",
            "confidence": 0.7,
        },
        "drawing.pdf",
        1,
    )

    assert fixture is not None
    assert fixture.quantity == 0
    assert fixture.wattage is None
    assert fixture.status == "review"


def test_values_embedded_in_ai_description_are_recovered() -> None:
    fixture = _fixture_from_ai(
        {
            "symbol": "LS-03",
            "description": (
                "2x10W LED (2x1040LM, CRI 85, 4000K, 36 degree beam angle) "
                "recessed adjustable trimless downlight, IP20, size 220 x 130 x 90 mm."
            ),
            "quantity": 0,
            "fixture_type": "downlight",
            "confidence": 0.4,
        },
        "drawing.pdf",
        1,
    )

    assert fixture is not None
    assert fixture.wattage == 20
    assert fixture.lumens == 2080
    assert fixture.cct == 4000
    assert fixture.cri == 85
    assert fixture.ip_rating == 20
    assert fixture.dimensions == "220 x 130 x 90 mm"
    assert fixture.mounting == "recessed"
    assert fixture.wattage_options == [20]
    assert fixture.lumen_options == [2080]
    assert fixture.beam_angle == "36°"


def test_pole_assembly_uses_individual_luminaire_wattage() -> None:
    fixture = _fixture_from_ai(
        {
            "symbol": "FL1",
            "description": (
                "12M HEIGHT POLE LIGHT WITH 4 X 400W LED LIGHT FIXTURE, IP66. "
                "INDIVIDUAL LUMINAIRE LUMEN OUTPUT SHALL BE MIN. 58070 lm."
            ),
            "fixture_type": "pole floodlight",
        },
        "drawing.pdf",
        1,
    )

    assert fixture is not None
    assert fixture.wattage == 400
    assert fixture.units_per_assembly == 4
    assert fixture.lumens == 58070
    assert fixture.lumens_is_minimum is True
    assert fixture.cct is None


def test_every_side_by_side_legend_gets_its_own_region() -> None:
    document = pymupdf.open()
    page = document.new_page(width=1000, height=600)
    page.insert_text((100, 350), "LEGEND")
    page.insert_text((100, 380), "SL1 50W STREET LIGHT")
    page.insert_text((500, 350), "LEGEND")
    page.insert_text((500, 380), "FL1 4 X 400W POLE LIGHT")

    regions = _legend_regions(page)

    assert len(regions) == 2
    assert regions[0].x1 <= regions[1].x1
    assert regions[0].x0 < 100 < regions[0].x1
    assert regions[1].x0 < 500 < regions[1].x1
    document.close()


def test_multi_option_and_strip_specs_are_preserved() -> None:
    fixture = _fixture_from_ai(
        {
            "symbol": "LS-05",
            "description": (
                "Recessed panel, WATTAGE: 25/40W, 3000LM/4800LM, "
                "3000K/4000K/6000K, AC 85-265V, IP43"
            ),
            "fixture_type": "panel luminaire",
        },
        "drawing.pdf",
        1,
    )

    assert fixture is not None
    assert fixture.wattage_options == [25.0, 40.0]
    assert fixture.wattage == 40
    assert fixture.lumen_options == [3000, 4800]
    assert fixture.cct_options == [3000, 4000, 6000]
    assert fixture.voltage == "AC 85V – 265V"

    strip = _fixture_from_ai(
        {
            "symbol": "LS-01",
            "description": "12VOLT LED strip, 60LEDS/M, NON WATERPROOF",
            "fixture_type": "LED strip",
        },
        "drawing.pdf",
        1,
    )
    assert strip is not None
    assert strip.voltage == "12VOLT"
    assert strip.led_density_per_m == 60
    assert strip.waterproof is False


def test_prompt_excludes_non_lighting_electrical_items() -> None:
    prompt = _prompt("drawing.pdf", 1, "LEGEND MOTION SENSOR SWITCH TYPE A1 20W")
    assert "LIGHT FIXTURES only" in prompt
    assert "Do not return switches" in prompt
    assert "Use null only for an unstated specification" in prompt
    assert "Do not count fixtures from the plan" in prompt


def test_legend_validation_does_not_treat_header_letters_as_fixture_rows() -> None:
    document = pymupdf.open()
    page = document.new_page(width=900, height=500)
    page.insert_text((500, 30), "LEGEND:")
    page.insert_text((500, 48), "SYMBOL")
    page.insert_text((505, 48), "B")  # Simulates a split one-letter header token.
    page.insert_text(
        (500, 75),
        "A1 TYPE A1 - LED LUMINAIRE SUSPENDED @IP20 53W 1449MMX60MM "
        "COATED STEEL BODY OPAL ACRYLIC DIFFUSER 6400LM +2800mm",
        fontsize=5,
    )
    page.insert_text(
        (500, 95),
        "A2 TYPE A2 - LED LUMINAIRE SUSPENDED @IP20 36W 1168MMX60MM "
        "COATED STEEL BODY OPAL ACRYLIC DIFFUSER 4650LM +3000mm",
        fontsize=5,
    )
    page.insert_text(
        (500, 115),
        "B TYPE B - LED LUMINAIRE RECESSED @IP20 40W SQUARE TYPE "
        "600MMX600MM SHEET STEEL BODY 4450LM +3000mm",
        fontsize=5,
    )
    fixtures = [
        FixtureRequirement(
            id=str(uuid.uuid4()),
            symbol=symbol,
            description=symbol,
            quantity=0,
            source_file="drawing.pdf",
        )
        for symbol in ("A1", "A2", "B")
    ]

    _validate_against_legend_rows(page, fixtures)

    assert fixtures[0].wattage == 53
    assert fixtures[0].lumens == 6400
    assert fixtures[1].wattage == 36
    assert fixtures[1].lumens == 4650
    assert fixtures[2].wattage == 40
    assert fixtures[2].lumens == 4450
    assert fixtures[2].cct is None
    document.close()
