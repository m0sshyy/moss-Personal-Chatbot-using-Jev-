"""Tests for brain.py: saving notes, links and files, and searching them by meaning."""

import asyncio
import io
import re
import zlib

import httpx
import pypdf
import pytest

import brain
import jev_router
import MosshyBot
import tracker


def word_vectors(texts, task):
    """Pretend embeddings: one number per word bucket, so texts sharing words are 'close'."""
    vectors = []
    for text in texts:
        vector = [0.0] * 64
        for word in re.findall(r"[a-z]+", text.lower()):
            vector[zlib.crc32(word.encode()) % 64] += 1
        vectors.append(vector)
    return vectors


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    calls = []

    def embed(texts, task):
        calls.append((len(texts), task))
        return word_vectors(texts, task)
    monkeypatch.setattr(brain, "embed", embed)
    monkeypatch.setattr(brain, "MIN_MATCH", 0.3)
    return calls


def test_short_text_is_one_piece():
    assert brain.chunks("  My locker code is 1408.  ") == ["My locker code is 1408."]
    assert brain.chunks("   ") == []


def test_long_text_is_cut_at_sentences_with_overlap():
    text = " ".join(f"Sentence number {n} talks about SHAP values." for n in range(200))
    pieces = brain.chunks(text)
    assert len(pieces) > 4
    assert all(len(piece) <= brain.CHUNK_CHARS for piece in pieces)
    assert all(piece.endswith(".") for piece in pieces)               # cut at a sentence end
    tail = pieces[0][-100:]
    assert tail.split(". ")[-1][:20] in pieces[1]                       # the pieces overlap


def test_saved_note_is_found_by_its_meaning(clock, fake_embeddings):
    brain.save_note("Locker", "My office locker code is 1408")
    brain.save_note("SHAP notes", "SHAP values show how much each feature pushes a prediction up or down")
    found = brain.search_brain("what is my locker code")
    assert found["results"][0]["title"] == "Locker"
    assert "1408" in found["results"][0]["text"] and found["results"][0]["source_no"] == 1
    assert "how_to_answer" in found
    assert fake_embeddings[-1] == (1, "RETRIEVAL_QUERY")


def test_nothing_close_enough_means_no_results(clock):
    brain.save_note("Locker", "My office locker code is 1408")
    found = brain.search_brain("recipe for chocolate cake")
    assert found["results"] == [] and "Nothing saved matches" in found["note"]


def test_pieces_of_one_document_come_back_together_in_order(clock, monkeypatch):
    monkeypatch.setattr(brain, "CHUNK_CHARS", 60)
    monkeypatch.setattr(brain, "OVERLAP", 10)
    brain.save("pdf", "XGBoost", "XGBoost builds trees one by one. Each new tree fixes "
                                 "the errors of the trees before. XGBoost trees use gradients.", source="file:x")
    results = brain.search("XGBoost trees", top=5)
    assert len(results) == 1 and "link" not in results[0]
    assert results[0]["text"].startswith("XGBoost builds")


def test_link_is_saved_once_with_its_text(clock, monkeypatch):
    monkeypatch.setattr(brain, "fetch_page", lambda url: ("RAG explained", "RAG finds pieces of text, then answers."))
    saved = brain.save_link("https://example.com/rag")
    assert saved["saved"] and saved["title"] == "RAG explained" and "RAG finds" in saved["start_of_page"]
    again = brain.save_link("https://example.com/rag")
    assert again["already_saved"] and again["item_id"] == saved["item_id"]
    result = brain.search("what does RAG find")[0]
    assert result["link"] == "https://example.com/rag"


def test_bad_links_explain_what_went_wrong(monkeypatch):
    assert "error" in brain.save_link("not a link")

    def blocked(url):
        raise httpx.HTTPStatusError("403 Forbidden", request=None, response=None)
    monkeypatch.setattr(brain, "fetch_page", blocked)
    assert "copy the text" in brain.save_link("https://example.com/private")["error"]


def test_page_text_skips_scripts_and_menus():
    title, text = brain.page_text("<html><head><title> My  Article </title><script>var x=1</script></head>"
                                  "<body><nav>Home | About</nav><h1>Hello</h1><p>First   para.</p>"
                                  "<p>Second para.</p><footer>© 2026</footer></body></html>")
    assert title == "My Article"
    assert text == "Hello\nFirst para.\nSecond para."


def test_list_and_forget(clock):
    first = brain.save_note("Locker", "Code 1408")["item_id"]
    clock.set(2026, 10, 13, 10, 0)
    brain.save_note("Wifi", "Office wifi password is in the pantry")
    listed = brain.list_brain()
    assert listed["total"] == 2 and [i["title"] for i in listed["newest_first"]] == ["Wifi", "Locker"]
    assert brain.forget_brain_item(first) == {"deleted": True, "title": "Locker"}
    assert [d.to_dict()["title"] for d in tracker._collection("brain_chunks").stream()] == ["Wifi"]
    assert "error" in brain.forget_brain_item(first)


def test_text_files_and_other_files():
    text, pages = asyncio.run(brain.read_document(b"hello notes", "notes.md", ""))
    assert (text, pages) == ("hello notes", 0)
    with pytest.raises(ValueError):
        asyncio.run(brain.read_document(b"PK..", "slides.pptx", "application/vnd.ms-powerpoint"))


def test_scanned_pdf_is_read_by_gemini(monkeypatch):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(200, 200)  # a page with no text, like a scan
    pdf = io.BytesIO()
    writer.write(pdf)

    async def gemini_reads(data):
        return "Text Gemini read from the scan"
    monkeypatch.setattr(brain, "read_scanned", gemini_reads)
    assert asyncio.run(brain.read_document(pdf.getvalue(), "scan.pdf", "application/pdf")) == \
        ("Text Gemini read from the scan", 1)


def test_moss_has_every_brain_tool():
    names = {tool.__name__ for tool in MosshyBot.chat_config.tools}
    assert jev_router.GROUPS["brain"] <= names
    assert "brain" in jev_router.QUESTIONS
