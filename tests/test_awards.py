from datetime import date

import pytest

from lss_report.awards import (
    CPR_C,
    FIRST_AID,
    LIFESAVING_INSTRUCTOR,
    NATIONAL_LIFEGUARD,
    OXYGEN,
    SWIM_INSTRUCTOR,
    add_years,
    columns_for,
    expiry_for,
)


@pytest.mark.parametrize(
    "title, expected",
    [
        ("National Lifeguard - Pool", ((NATIONAL_LIFEGUARD, False),)),
        ("National Lifeguard - Pool Recert", ((NATIONAL_LIFEGUARD, False),)),
        ("National Lifeguard - Waterfront", ((NATIONAL_LIFEGUARD, False),)),
        ("City of Calgary Staff NL Recert", ((NATIONAL_LIFEGUARD, False),)),
        ("Swim Instructor", ((SWIM_INSTRUCTOR, False),)),
        ("Swim Instructor Recert", ((SWIM_INSTRUCTOR, False),)),
        ("Lifesaving Instructor/Examiner", ((LIFESAVING_INSTRUCTOR, False),)),
        ("O2 Administration", ((OXYGEN, False),)),
        ("Standard First Aid", ((FIRST_AID, False),)),
    ],
)
def test_tracked_awards_map_to_columns(title, expected):
    assert columns_for(title) == expected


def test_cpr_only_award_counts_for_first_aid_provisionally():
    assert columns_for("Lifesaving CPR C & AED") == ((CPR_C, False), (FIRST_AID, True))


def test_combined_first_aid_and_cpr_award_certifies_both_outright():
    assert columns_for("Intermediate First Aid CPR C & AED  Recert") == (
        (FIRST_AID, False),
        (CPR_C, False),
    )


def test_red_cross_titles_certify_first_aid_and_cpr_outright():
    # The Red Cross writes CPR Level C as "CPR/AED Level C", the Society as "CPR C".
    assert columns_for("Standard First Aid CPR/AED Level C (Blended)") == (
        (FIRST_AID, False),
        (CPR_C, False),
    )
    assert columns_for("Emergency First Aid CPR/AED Level C") == (
        (FIRST_AID, False),
        (CPR_C, False),
    )


def test_a_red_cross_cpr_only_award_still_counts_for_first_aid_provisionally():
    assert columns_for("CPR/AED Level C") == ((CPR_C, False), (FIRST_AID, True))


def test_a_lower_cpr_level_is_not_read_as_level_c():
    assert columns_for("Standard First Aid CPR/AED Level A") == ((FIRST_AID, False),)


@pytest.mark.parametrize(
    "title",
    [
        "Lifesaving CPR Instructor/Examiner",
        "National Lifeguard Instructor",
        "2023 National Lifeguard Update",
        "Lifesaving Inst Leadership Update 11",
        "SwimAbilities Instructor",
        "Swim Trainer Recert",
        "Lifesaving Sport Coach 1",
        "Bronze Cross",
        "Bronze Medallion",
        "Exam Standards Clinic",
        "Next Wave Conference 2026",
        "Stroke Proficiency",
        "Pool Official",
        "Shallow Water Attendant",
    ],
)
def test_recognised_but_untracked_awards_map_to_nothing(title):
    assert columns_for(title) == ()


@pytest.mark.parametrize(
    "title",
    ["Wilderness Guide Level 4", "Lifesaving CPR B", "Basic Life Support"],
)
def test_unknown_award_is_reported_as_unmapped(title):
    assert columns_for(title) is None


def test_first_aid_uses_the_two_year_house_policy_not_three():
    assert FIRST_AID.validity_years == 2
    assert expiry_for(FIRST_AID, date(2024, 2, 10)) == date(2026, 2, 10)


def test_cpr_and_first_aid_expire_on_different_schedules():
    certified = date(2025, 9, 14)
    assert expiry_for(CPR_C, certified) == date(2026, 9, 14)
    assert expiry_for(FIRST_AID, certified) == date(2027, 9, 14)


def test_leap_day_expiry_falls_back_to_the_28th():
    assert add_years(date(2024, 2, 29), 2) == date(2026, 2, 28)
    assert add_years(date(2024, 2, 29), 4) == date(2028, 2, 29)
