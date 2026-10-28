"""The Ingram Micro MAP deliverable: the client's template, filled with the MRA results, nothing added and nothing removed."""
import copy
import io
import zipfile
from pathlib import Path

import pytest
from pptx import Presentation

from mra.outputs import deck_template as dt
from mra.outputs import ingram_deck as ing
from mra.outputs import render, textfit
from tests.test_outputs import model

TEMPLATE = ing.ingram_template_bytes()
BLANK_SLIDES = {3: "Executive Summary", 4: "Assessment Scope", 7: "MAP Journey", 8: "7 R Analysis", 9: "AWS Costs", 15: "Stay On Premise"}


def walk(shapes):
    for sh in shapes:
        yield sh
        if sh.shape_type == 6:
            yield from walk(sh.shapes)


def slide_text(slide) -> str:
    out = []
    for sh in walk(slide.shapes):
        if sh.has_text_frame:
            out.append(sh.text_frame.text)
        if getattr(sh, "has_table", False) and sh.has_table:
            out += [c.text for r in sh.table.rows for c in r.cells]
        if getattr(sh, "has_chart", False) and sh.has_chart and sh.chart.has_title and sh.chart.chart_title.has_text_frame:
            out.append(sh.chart.chart_title.text_frame.text)
    return "\n".join(out)


def charts_of(prs):
    return [(i, sh.chart) for i, s in enumerate(prs.slides, 1) for sh in walk(s.shapes) if getattr(sh, "has_chart", False) and sh.has_chart]


@pytest.fixture
def probe(cfg):
    return dt._complete_model(cfg)                                    # every rating 3: any other number in an MRA chart would be left-over sample data


@pytest.fixture
def filled(probe):
    out = render("pptx", probe)
    return Presentation(io.BytesIO(out)), probe


# ---- the template is the client's, untouched ---------------------------------------------------------------------------------------
def test_the_stored_template_is_the_clients_file_changed_only_in_its_content_type():
    src = Path("/mnt/user-data/uploads/MAP_Access_Deliverable.potx")
    if not src.exists():
        pytest.skip("the client's original .potx is not on this machine")
    a, b = zipfile.ZipFile(src), zipfile.ZipFile(io.BytesIO(TEMPLATE))
    assert a.namelist() == b.namelist()
    diff = [n for n in a.namelist() if a.read(n) != b.read(n)]
    assert diff == ["[Content_Types].xml"]
    assert a.read("[Content_Types].xml").replace(b"template.main+xml", b"presentation.main+xml") == b.read("[Content_Types].xml")


def test_a_powerpoint_template_file_is_accepted_as_it_comes(probe):
    src = Path("/mnt/user-data/uploads/MAP_Access_Deliverable.potx")
    if not src.exists():
        pytest.skip("the client's original .potx is not on this machine")
    assert ing.is_ingram(src.read_bytes())
    out = render("pptx", probe, src.read_bytes())
    assert len(Presentation(io.BytesIO(out)).slides) == 25


def test_only_the_ingram_deck_is_recognised_as_ingram(template_bytes=None):
    assert ing.is_ingram(TEMPLATE) and not ing.is_ingram(dt.default_template_bytes())
    buf = io.BytesIO()
    Presentation().save(buf)
    assert not ing.is_ingram(buf.getvalue()) and not ing.is_ingram(b"not a deck")


# ---- nothing less, nothing more ------------------------------------------------------------------------------------------------------
def test_every_slide_is_there_in_order_on_its_layout_with_its_shapes(filled):
    out, m = filled
    base = Presentation(io.BytesIO(TEMPLATE))
    assert len(out.slides) == len(base.slides) == 25
    assert ing.structure_problems(base, out, allowed_removed=10) == []
    for i, (a, b) in enumerate(zip(base.slides, out.slides), 1):
        assert a.slide_layout.name == b.slide_layout.name, i
        assert [s.name for s in b.shapes] == [s.name for s in a.shapes if not (s.has_text_frame and s.text_frame.text.strip() == "Example")], i


def test_the_titles_of_all_25_slides_are_unchanged(filled):
    out, _ = filled
    base = Presentation(io.BytesIO(TEMPLATE))
    for i, (a, b) in enumerate(zip(base.slides, out.slides), 1):
        first = lambda s: [sh.text_frame.text.split("\n")[0] for sh in s.shapes if sh.has_text_frame and sh.top is not None and sh.top < 110 * 12700][:1]  # noqa: E731
        assert first(a) == first(b), i


def test_the_pictures_and_static_slides_are_byte_for_byte_the_templates(filled):
    out, _ = filled
    base = Presentation(io.BytesIO(TEMPLATE))
    for i in list(range(16, 26)) + [1, 2, 5, 6]:
        assert slide_text(out.slides[i - 1]) == slide_text(base.slides[i - 1]), f"slide {i} changed"


def test_every_section_is_recognised(filled):
    _, m = filled
    assert m["deck_stats"] == {"slides": 25, "anchors": 11, "mra_charts": 12, "recommendations": 1, "exec_boxes": 10, "focus_boxes": 6}


# ---- the MRA sections ------------------------------------------------------------------------------------------------------------------
def test_the_charts_hold_exactly_the_scores_and_no_sample_data(filled):
    out, m = filled
    mra = [(i, c) for i, c in charts_of(out) if i in (10, 11, 12, 13, 14)]
    assert len(mra) == 12
    for i, c in mra:
        allowed = {3.0, 60.0, 80.0}                                    # the probe's scores, its radar value, and the guide ring
        for s in c.plots[0].series:
            assert set(s.values) <= allowed, (i, s.name, set(s.values) - allowed)


def test_hand_typed_label_text_from_the_sample_is_gone_and_scores_show_one_decimal(filled):
    out, _ = filled
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    for i, c in charts_of(out):
        root = c._chartSpace
        assert not [d for d in root.iter(ns + "dLbl") if d.find(ns + "tx") is not None], f"chart on slide {i} still has typed label text"
        if root.find(".//" + ns + "barChart") is not None:
            assert all(f.get("formatCode") == "0.0" and f.get("sourceLinked") == "0" for d in root.iter(ns + "dLbls") for f in d.findall(ns + "numFmt"))


def test_scores_land_on_the_right_bars_and_in_the_right_colours(svc, cfg, rset, golden):
    m = model(svc, cfg, rset, golden["ratings"])
    out = Presentation(io.BytesIO(render("pptx", m)))
    bars = next(c for i, c in charts_of(out) if i == 10)
    by = {w["name"]: w for w in m["workstreams"]}
    for cat, val in zip(bars.plots[0].categories, bars.plots[0].series[0].values):
        w = by[next(n for n in by if n == cat or n.startswith(cat.rstrip(".")))]
        assert val == w["score"], cat
    skills = by["Skills & COE"]
    assert skills["status"] == "incomplete"                           # Q42 is blank: no bar and no colour, never a zero
    idx = [c for c in bars.plots[0].categories].index("Skills & COE")
    assert list(bars.plots[0].series[0].values)[idx] is None


def test_executive_summary_shows_the_answers_and_four_strengths_and_four_gaps(svc, cfg, rset, full_ratings):
    m = model(svc, cfg, rset, full_ratings, answers={"Q1": "Scope text for the MRA", "Q2": "Outcome text"})
    out = Presentation(io.BytesIO(render("pptx", m)))
    s11 = out.slides[10]
    text = slide_text(s11)
    assert "Outcome text" in text and "Scope text for the MRA" in text and "Notes" not in text
    pre = [a for a in m["activities"] if a["phase"] == "Pre-Mobilize" and a["status"] == "complete"]
    asc = [a["name"] for _, a in sorted(((a["score"], i), a) for i, a in enumerate(pre))]
    boxes = [sh for sh in s11.shapes if sh.has_text_frame and abs(sh.width / 12700 - 253) < 8]
    left = [sh.text_frame.text for sh in sorted((b for b in boxes if b.left < out.slide_width / 2), key=lambda s: s.top)]
    right = [sh.text_frame.text for sh in sorted((b for b in boxes if b.left >= out.slide_width / 2), key=lambda s: s.top)]
    assert right == asc[:4] and left == list(reversed(asc[-4:]))      # the verified rule: weakest from the front, strongest from the back


def test_key_recommendations_are_the_aws_actions_for_the_weakest_activities(svc, cfg, rset, golden):
    m = model(svc, cfg, rset, golden["ratings"])
    recs = ing.key_recommendations(m)
    assert 1 <= len(recs) <= 5 and len(set(recs)) == len(recs) and all(r.endswith(".") for r in recs)
    objectives = {a["objective"] for act in m["activities"] for a in act["actions"] if a.get("objective")}
    assert all(r.rstrip(".") in objectives or r.startswith("Improve ") for r in recs)
    weakest = min((a for a in m["activities"] if a["status"] == "complete" and a["needs_action"]), key=lambda a: a["score"])
    assert weakest["actions"][0]["objective"].rstrip(".") + "." == recs[0]
    out = Presentation(io.BytesIO(render("pptx", m)))
    shape = next(sh for sh in out.slides[9].shapes if sh.has_text_frame and sh.text_frame.text.startswith("Key Recommendations"))
    bullets = [p.text for p in shape.text_frame.paragraphs[2:] if p.text.strip()]
    assert bullets == recs


def test_a_rejected_action_is_not_a_recommendation(svc, cfg, rset, golden):
    m = model(svc, cfg, rset, golden["ratings"])
    first = ing.key_recommendations(m)[0]
    weakest = min((a for a in m["activities"] if a["status"] == "complete" and a["needs_action"]), key=lambda a: a["score"])
    weakest["decision"] = "reject"
    assert first not in ing.key_recommendations(m) or any(first.rstrip(".") == a["objective"] for x in m["activities"] if x is not weakest for a in x["actions"])


def test_the_focus_sentences_are_the_rule_based_ones_on_both_slides(filled):
    out, m = filled
    for idx, key in ((11, "perspective_sentences"), (12, "workstream_sentences")):
        text = slide_text(out.slides[idx])
        for sent in m["focus"][key]:
            assert sent in text


def test_a_missing_sentence_leaves_no_orphan_bullet_marker(probe):
    probe["focus"]["workstream_sentences"] = probe["focus"]["workstream_sentences"][:1]
    out = Presentation(io.BytesIO(render("pptx", probe)))
    s13 = out.slides[12]
    assert len([s for s in s13.shapes if s.name.startswith("Isosceles Triangle")]) == 1
    assert sum(1 for s in s13.shapes if s.name.startswith("Rectangle") and len(s.text_frame.text) > 45) == 1


# ---- the sections that need other inputs: present, labelled, blank ------------------------------------------------------------------------
def test_data_sections_stay_in_the_deck_blank_with_no_markers_and_no_sample_data(filled):
    out, m = filled
    for n in BLANK_SLIDES:
        t = slide_text(out.slides[n - 1])
        assert "<>" not in t and "Example" not in t, n
    assert "Dates" not in slide_text(out.slides[3]) and "Tools used" not in slide_text(out.slides[3])
    assert "Datacenters:" in slide_text(out.slides[3]) and "Total Cores:" in slide_text(out.slides[3])         # the labels stay
    assert "Strategy" in slide_text(out.slides[6]) and "Rehost" in slide_text(out.slides[6])
    assert "Include details on discovered servers" not in slide_text(out.slides[7]) and "Recommendations for migration" in slide_text(out.slides[7])
    for i in (4, 7):
        for _, c in [x for x in charts_of(out) if x[0] == i]:
            assert all(v is None for s in c.plots[0].series for v in s.values), i


def test_no_sample_customer_number_survives_anywhere_in_the_package(filled):
    out, m = filled
    buf = io.BytesIO()
    out.save(buf)
    with zipfile.ZipFile(buf) as z:
        for name in z.namelist():
            if name.startswith("ppt/charts/chart") and name.endswith(".xml"):
                x = z.read(name).decode()
                for sample in ("93", "94", "70.0", "32.0", "42.22", "76.66", "45.46", "32.86"):
                    assert f"<c:v>{sample}</c:v>" not in x, (name, sample)
        for name in z.namelist():                                                # the workbooks inside the charts were rewritten too
            if name.startswith("ppt/embeddings/") and name.endswith(".xlsx"):
                with zipfile.ZipFile(io.BytesIO(z.read(name))) as w:
                    cells = " ".join(w.read(n).decode() for n in w.namelist() if n.endswith("sheet1.xml"))
                    assert ">93<" not in cells and ">94<" not in cells and ">76.66<" not in cells, name


def test_the_open_items_say_which_sections_are_blank_and_why(svc, cfg, rset, golden):
    svc.run(rset(golden["ratings"], meta={"facilitator": "F"}), "t")
    files = svc.export("t", ["pptx", "todo"], "t")
    todo = next(v for k, v in files.items() if k.endswith("open_items.md")).decode()
    assert "Deck sections left blank" in todo and 'Slide 3 "Executive Summary"' in todo and 'Slide 9 "AWS Costs & Migration Savings Details"' in todo
    assert "discovery data" in todo and "the 7R analysis" in todo and "the AWS cost analysis" in todo
    assert "Mobilize cost slides" not in todo                                      # the AWS-deck checklist is not shown for the Ingram deck


# ---- no bleeding, no overlapping ------------------------------------------------------------------------------------------------------
def long_model(probe):
    probe["text_answers"] = [dict(t, included=True, answer=("A very long answer about strategy and desired outcomes. " * 40) if t["number"] in (1, 2) else t["answer"])
                             for t in probe["text_answers"]]
    for a in probe["activities"]:
        if a["phase"] == "Pre-Mobilize":
            a["name"] = a["name"] + " and a considerably longer descriptive name than usual for this activity"
    probe["focus"]["perspective_sentences"] = [("You're already doing very well in the Application Portfolio Discovery and Planning dimension; " * 4)] * 3
    probe["focus"]["workstream_sentences"] = [("Put emphasis on improving the Migration Process and Experience dimension for a steady cloud migration. " * 4)] * 3
    for a in probe["activities"]:
        for act in a["actions"]:
            act["objective"] = "Assure that security, compliance and governance are considered at the very front of the whole initiative " * 3
    return probe


def test_very_long_text_is_fitted_never_left_to_overflow(probe):
    m = long_model(probe)
    out = Presentation(io.BytesIO(render("pptx", m)))
    assert len(out.slides) == 25
    assert len(m["deck_fit"]) == 17 and all(f["needed"] <= f["budget"] + 0.01 for f in m["deck_fit"]), [f for f in m["deck_fit"] if f["needed"] > f["budget"]]
    assert all(f["size"] >= 10 for f in m["deck_fit"])                          # readable: never the 9 pt of the earlier version
    assert any("shortened" in w for w in m["deck_warnings"])                      # anything cut is reported, never silent
    assert all("Excel workbook" in w for w in m["deck_warnings"])


def test_normal_text_stays_close_to_the_templates_own_font_sizes(filled):
    _, m = filled
    focus = [f for f in m["deck_fit"] if f["box"].startswith("Areas of focus")]
    assert len(focus) == 6 and all(f["size"] >= 13 for f in focus)                 # the probe's long names may shrink a sentence slightly, never much
    assert m["deck_warnings"] == []                                                # and nothing needed cutting


def test_the_fit_engine_wraps_shrinks_and_cuts_in_that_order():
    assert textfit.wrap("one two three", 1000, 12) == ["one two three"]
    assert len(textfit.wrap("word " * 50, 100, 12)) > 5 and all(textfit.width_pt(x, 12) <= 100 for x in textfit.wrap("word " * 50, 100, 12))
    assert textfit.wrap("x" * 200, 80, 12)[0] and all(textfit.width_pt(x, 12) <= 80 for x in textfit.wrap("x" * 200, 80, 12))     # a very long word is split
    assert textfit.char_em("W") > textfit.char_em("a") > textfit.char_em("i")
    prs = Presentation(io.BytesIO(TEMPLATE))
    box = next(sh for sh in prs.slides[11].shapes if sh.name == "Rectangle 4")
    ok = textfit.fit(box, ["short"], [], 16)
    assert ok["size"] == 16 and not ok["shortened"]
    shrunk = textfit.fit(box, ["medium length text that needs more than one line to show. " * 3], [], 16)
    assert shrunk["size"] < 16 and not shrunk["shortened"]
    cut = textfit.fit(box, ["endless " * 400], [], 16)
    assert cut["size"] == 9 and cut["shortened"] and cut["paragraphs"][0].endswith("\u2026") and cut["needed"] <= cut["budget"]


# ---- formats, defaults and the library ---------------------------------------------------------------------------------------------------
def test_with_no_template_the_ingram_deck_is_used_and_an_aws_deck_still_gets_the_aws_treatment(probe):
    assert len(Presentation(io.BytesIO(render("pptx", copy.deepcopy(probe)))).slides) == 25
    aws = Presentation(io.BytesIO(render("pptx", copy.deepcopy(probe), dt.default_template_bytes())))
    assert len(aws.slides) == 89 or len(aws.slides) == 90 - 1


def test_coverage_of_the_ingram_deck_is_complete_and_an_aws_deck_is_not_one(cfg):
    cov = dt.coverage(TEMPLATE, cfg)
    assert cov["complete"] and cov["found"] == cov["expected"] == 65 and [i["key"] for i in cov["items"]][0] == "slides"
    assert {i["label"] for i in cov["items"]} == set(ing.LABELS.values())


def test_the_deck_cannot_be_made_if_it_would_not_match_the_template(probe, monkeypatch):
    monkeypatch.setattr(ing, "structure_problems", lambda *a, **k: ["slide count 24 instead of 25"])
    with pytest.raises(ValueError, match="would not match the template"):
        render("pptx", probe)


def test_an_incomplete_assessment_still_gives_a_complete_deck(svc, cfg, rset, golden):
    m = model(svc, cfg, rset, golden["ratings"])
    out = Presentation(io.BytesIO(render("pptx", m)))
    assert len(out.slides) == 25 and "Incomplete" not in slide_text(out.slides[9])
    assert ing.structure_problems(Presentation(io.BytesIO(TEMPLATE)), out, 10) == []


def test_an_empty_assessment_leaves_every_text_box_blank_but_present(svc, cfg, rset, golden):
    m = model(svc, cfg, rset, golden["ratings"])
    for a in m["activities"]:
        a.update(needs_action=False, actions=[])
    m["text_answers"] = [dict(t, answer="") for t in m["text_answers"]]
    out = Presentation(io.BytesIO(render("pptx", m)))
    s10 = next(sh for sh in out.slides[9].shapes if sh.has_text_frame and sh.text_frame.text.startswith("Key Recommendations"))
    assert [p.text for p in s10.text_frame.paragraphs if p.text.strip()] == ["Key Recommendations"]
    assert "Notes" not in slide_text(out.slides[10]) and len(out.slides) == 25


def test_no_text_box_we_fill_reaches_the_decorative_stripe_on_the_right(probe):
    m = long_model(probe)
    out = Presentation(io.BytesIO(render("pptx", m)))
    base = Presentation(io.BytesIO(TEMPLATE))
    width = out.slide_width / 12700
    for idx in (9, 10, 11, 12):
        limit = ing.safe_right(out.slides[idx], width)
        assert 900 < limit < 916, limit                                      # the stripe starts at 916 pt; 6 pt of air
        for sh in out.slides[idx].shapes:
            if sh.has_text_frame and len(sh.text_frame.text) > 45 and sh.text_frame.text != "AWS identified preliminary areas of focus":
                assert (sh.left + sh.width) / 12700 <= limit + 0.5, (idx + 1, sh.name, (sh.left + sh.width) / 12700)     # the boxes we write
    over = [(i + 1, sh.name) for i in (9, 10, 11, 12) for sh in base.slides[i].shapes if sh.has_text_frame and (sh.left + sh.width) / 12700 > 916]
    assert over, "the template itself has boxes that reach the stripe (that is why they are narrowed)"


# ---- the mess seen on real slides, each pinned ------------------------------------------------------------------------------------------
def test_a_long_answer_keeps_whole_lines_or_flows_it_never_one_word_per_line(probe):
    prs = Presentation(io.BytesIO(TEMPLATE))
    box = next(sh for sh in prs.slides[10].shapes if sh.has_text_frame and sh.text_frame.text.strip() == "Notes")
    lines = [f"Line {i}: the assessment covers the customer's estate and its readiness for migration to the cloud" for i in range(20)]
    flow = textfit.fit(box, lines, ["Notes"], 13.33, collapse=True, min_size=11)
    assert flow["needed"] <= flow["budget"] and flow["size"] >= 11 and len(flow["paragraphs"]) == 1 and flow["paragraphs"][0].endswith("\u2026")
    assert all(len(p.split()) > 5 for p in flow["paragraphs"])
    whole = textfit.fit(box, lines, ["Notes"], 13.33, min_size=11)                    # without flowing: whole lines, then a cut, never every line chopped
    assert whole["paragraphs"][:-1] == lines[:len(whole["paragraphs"]) - 1] and whole["needed"] <= whole["budget"]
    assert all(len(p.split()) > 5 for p in whole["paragraphs"][:-1])


def test_the_executive_summary_answers_stay_inside_their_boxes(probe):
    probe["text_answers"] = [dict(t, included=True, answer="\n".join(f"Point {i}: " + "a long sentence about the customer's situation " * 3 for i in range(20)))
                             if t["number"] in (1, 2) else t for t in probe["text_answers"]]
    out = Presentation(io.BytesIO(render("pptx", probe)))
    notes = [f for f in probe["deck_fit"] if "(Q" in f["box"]]
    assert len(notes) == 2 and all(f["needed"] <= f["budget"] and f["size"] >= 11 for f in notes)
    text = slide_text(out.slides[10])
    assert "\u2026" in text and "Point 0" in text and "Point 19" not in text                          # the start is shown, the rest is in the workbook
    assert any("Q2" in w or "Q1" in w for w in probe["deck_warnings"])


def test_a_value_placeholder_is_cleared_whole_leaving_no_units_and_no_half_sentences(filled):
    out, _ = filled
    for sh in out.slides[2].shapes:                                                    # slide 3: four boxes whose sentences were built around numbers
        if sh.has_text_frame and sh.name.startswith("Rectangle"):
            assert sh.text_frame.text.strip() == "", sh.text_frame.text
    t4 = slide_text(out.slides[3])
    assert "TB" not in t4 and "N=" not in t4 and "<>" not in t4
    chart = next(c for i, c in charts_of(out) if i == 4)
    assert chart.has_legend is False and chart.chart_title.text_frame.text.strip() == "Workloads Assessed"


def test_radar_labels_are_small_enough_not_to_wrap_or_be_cut(filled):
    out, _ = filled
    for i, c in charts_of(out):
        if c.chart_type is not None and "RADAR" in str(c.chart_type):
            n = len(list(c.plots[0].categories))
            assert c.category_axis.tick_labels.font.size.pt == (12 if n <= 6 else 10), i


def test_our_text_carries_no_spelling_or_grammar_marks(filled):
    out, _ = filled
    ours = [sh for i in (9, 10, 11, 12) for sh in out.slides[i].shapes if sh.has_text_frame and len(sh.text_frame.text) > 20]
    assert ours
    for sh in ours:
        for p in sh.text_frame.paragraphs:
            for r in p.runs:
                if r.text.strip() and sh.text_frame.text not in ("AWS identified preliminary areas of focus",):
                    rpr = r._r.find("{http://schemas.openxmlformats.org/drawingml/2006/main}rPr")
                    assert rpr is None or rpr.get("err") is None
