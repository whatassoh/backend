"""Deterministic structure-aware document chunking.

Chunk boundaries are intentionally independent from an LLM. This keeps source
lineage reproducible and prevents model drift from silently changing citations.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(?:#{1,6}\s+|[A-ZÀ-ÖØ-Ý][^.!?]{2,80}:\s*$)")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ0-9])")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    index: int
    content: str
    content_hash: str
    section_path: str
    token_count: int


def normalize_document(content: str) -> str:
    value = unicodedata.normalize("NFKC", content or "")
    value = "".join(
        character for character in value if unicodedata.category(character) != "Cf"
    )
    value = CONTROL_RE.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class DocumentChunker:
    """Build bounded chunks while retaining headings and modest context overlap."""

    def __init__(
        self,
        *,
        target_characters: int = 1_200,
        max_characters: int = 1_800,
        overlap_characters: int = 160,
    ) -> None:
        if not 200 <= target_characters <= max_characters:
            raise ValueError("Invalid knowledge chunk target.")
        if not 0 <= overlap_characters < target_characters:
            raise ValueError("Invalid knowledge chunk overlap.")
        self.target_characters = target_characters
        self.max_characters = max_characters
        self.overlap_characters = overlap_characters

    def split(self, content: str) -> list[DocumentChunk]:
        normalized = normalize_document(content)
        if not normalized:
            return []
        units = self._units(normalized)
        raw_chunks: list[tuple[str, str]] = []
        current: list[str] = []
        current_section = ""

        for section, unit in units:
            if current and section and section != current_section:
                raw_chunks.append((current_section, "\n\n".join(current)))
                current = []
            current_section = section or current_section
            for fragment in self._hard_split(unit):
                candidate = "\n\n".join([*current, fragment]).strip()
                if current and len(candidate) > self.target_characters:
                    previous = "\n\n".join(current)
                    raw_chunks.append((current_section, previous))
                    overlap = self._overlap(previous)
                    current = (
                        [overlap]
                        if overlap
                        and len(overlap) + len(fragment) + 2 <= self.max_characters
                        else []
                    )
                    candidate = "\n\n".join([*current, fragment]).strip()
                if current and len(candidate) > self.max_characters:
                    raw_chunks.append((current_section, "\n\n".join(current)))
                    current = []
                current.append(fragment)
        if current:
            raw_chunks.append((current_section, "\n\n".join(current)))

        chunks: list[DocumentChunk] = []
        seen: set[str] = set()
        for section, text in raw_chunks:
            text = text.strip()
            digest = hashlib.sha256(text.encode()).hexdigest()
            if not text or digest in seen:
                continue
            seen.add(digest)
            chunks.append(
                DocumentChunk(
                    index=len(chunks),
                    content=text,
                    content_hash=digest,
                    section_path=section[:500],
                    token_count=max(1, (len(text) + 3) // 4),
                )
            )
        return chunks

    @staticmethod
    def _units(content: str) -> list[tuple[str, str]]:
        section = ""
        units: list[tuple[str, str]] = []
        block_lines: list[str] = []

        def flush_block() -> None:
            block = " ".join(block_lines).strip()
            for sentence in SENTENCE_RE.split(block):
                if sentence.strip():
                    units.append((section, sentence.strip()))

        for line in (*content.splitlines(), ""):
            if HEADING_RE.match(line):
                flush_block()
                block_lines = []
                section = line.lstrip("# ").rstrip(":").strip()
            elif not line.strip():
                flush_block()
                block_lines = []
            else:
                block_lines.append(line)
        return units

    def _hard_split(self, unit: str) -> list[str]:
        return [
            unit[start : start + self.max_characters]
            for start in range(0, len(unit), self.max_characters)
        ]

    def _overlap(self, text: str) -> str:
        if not self.overlap_characters:
            return ""
        tail = text[-self.overlap_characters :]
        boundary = tail.find(" ")
        return tail[boundary + 1 :] if boundary >= 0 else tail


def chunk_content(content: str, *, max_characters: int = 800) -> list[str]:
    """Backward-compatible helper used by callers that only need text chunks."""

    target = max(200, max_characters)
    return [
        chunk.content
        for chunk in DocumentChunker(
            target_characters=target,
            max_characters=target,
            overlap_characters=0,
        ).split(content)
    ]
