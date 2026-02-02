"""Plan chunking for large file support."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class PlanSection:
    """A logical section of a plan."""
    title: str
    level: int
    start_line: int
    end_line: int
    content: str


@dataclass
class PlanChunk:
    """A processable chunk of a plan."""
    id: str
    sections: List[PlanSection]
    estimated_tokens: int
    dependencies: List[str]  # Other chunk IDs this depends on


class PlanChunker:
    """Breaks large plans into processable chunks."""

    CHUNK_TARGET_TOKENS = 2000  # Target tokens per chunk (input only)
    CHUNK_MAX_TOKENS = 2500  # Hard limit per chunk (input only)

    # Estimated output multiplier: 1 input token generates ~2-3 output tokens for task DAG
    OUTPUT_MULTIPLIER = 2.5

    def __init__(self, plan_path: Path):
        self.plan_path = plan_path

    def analyze(self) -> dict:
        """Analyze plan and return metadata."""
        with open(self.plan_path) as f:
            lines = f.readlines()

        sections = self._extract_sections(lines)
        total_tokens = sum(len(s.content) / 4 for s in sections)

        return {
            "total_tokens": total_tokens,
            "section_count": len(sections),
            "needs_chunking": total_tokens > self.CHUNK_TARGET_TOKENS,
            "sections": sections,
        }

    def _extract_sections(self, lines: List[str]) -> List[PlanSection]:
        """Extract sections from markdown plan."""
        sections = []
        current_section = None
        current_content = []
        current_start = 0

        for i, line in enumerate(lines):
            if line.startswith("## ") or line.startswith("### "):
                # Save previous section
                if current_section:
                    current_section.end_line = i
                    current_section.content = "".join(current_content)
                    sections.append(current_section)

                # Start new section
                level = 2 if line.startswith("## ") else 3
                title = line.lstrip("#").strip()
                current_section = PlanSection(
                    title=title,
                    level=level,
                    start_line=i,
                    end_line=0,  # Will be set when section ends
                    content=""
                )
                current_content = [line]
                current_start = i
            else:
                if current_section is not None:
                    current_content.append(line)
                else:
                    # Content before first section goes to intro
                    pass

        # Save last section
        if current_section:
            current_section.end_line = len(lines)
            current_section.content = "".join(current_content)
            sections.append(current_section)

        return sections

    def create_chunks(self, metadata: dict) -> List[PlanChunk]:
        """Create processable chunks from plan sections."""
        if not metadata["needs_chunking"]:
            # Single chunk
            return [
                PlanChunk(
                    id="chunk-1",
                    sections=metadata["sections"],
                    estimated_tokens=metadata["total_tokens"],
                    dependencies=[],
                )
            ]

        sections = metadata["sections"]
        chunks = []
        current_chunk_sections = []
        current_tokens = 0
        chunk_num = 1

        for section in sections:
            section_tokens = len(section.content) / 4

            # Check if we need to start a new chunk
            if (
                current_tokens + section_tokens > self.CHUNK_MAX_TOKENS
                and current_chunk_sections
            ):
                # Finalize current chunk
                chunks.append(
                    PlanChunk(
                        id=f"chunk-{chunk_num}",
                        sections=current_chunk_sections,
                        estimated_tokens=current_tokens,
                        dependencies=[f"chunk-{chunk_num - 1}"] if chunk_num > 1 else [],
                    )
                )

                # Start new chunk
                chunk_num += 1
                current_chunk_sections = [section]
                current_tokens = section_tokens
            else:
                current_chunk_sections.append(section)
                current_tokens += section_tokens

        # Don't forget last chunk
        if current_chunk_sections:
            chunks.append(
                PlanChunk(
                    id=f"chunk-{chunk_num}",
                    sections=current_chunk_sections,
                    estimated_tokens=current_tokens,
                    dependencies=[f"chunk-{chunk_num - 1}"] if chunk_num > 1 else [],
                )
            )

        logger.info(f"Split plan into {len(chunks)} chunks")
        for chunk in chunks:
            logger.info(
                f"  {chunk.id}: {chunk.estimated_tokens:.0f} tokens, "
                f"{len(chunk.sections)} sections"
            )

        return chunks

    def write_chunk(self, chunk: PlanChunk, output_path: Path):
        """Write a chunk to a file."""
        content = "\n".join(s.content for s in chunk.sections)
        output_path.write_text(content)
