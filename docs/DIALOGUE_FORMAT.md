# Complete Box dialogue-data notes

This note records a static, read-only analysis of the main launcher and the
four game executables: TR, EX, SECOND, and THIRD.  It describes the bytecode
that the game actually renders; it is **not Shift-JIS**.

## Result

The scenario, battle, and death-quote files are not compressed message blobs.
They contain tables/records plus the renderer's bytecode in plain form.  The
main launcher and all four PS-X executables also contain embedded records using
the same bytecode.  Because executable scans mix code and data, executable
results are heuristic; the pointer-delimited archive results are much cleaner.

The high-confidence filter implemented by
`tools/extract_dialogue_candidates.py` finds 68,897 quoted records across all
five executables and nine message files.  Of those, 67,659 are in the
pointer-delimited SCE/DEAD/BMESS files.

| Game | Source | Role | High-confidence candidates |
| --- | --- | --- | ---: |
| main | `SLPS_020.70` | launcher executable | 271 |
| TR | `TR.WAR` | game executable | 264 |
| EX | `EX/EX.WAR` | game executable | 262 |
| EX | `EX/E_SCE.BIN` | scenario dialogue | 5,243 |
| EX | `EX/E_DEAD.BIN` | defeat/death quotes | 115 |
| EX | `BMESS4.BIN` | battle messages | 18,880 |
| SECOND | `SECOND/SECOND.WAR` | game executable | 221 |
| SECOND | `SECOND/2_SCE.BIN` | scenario dialogue | 4,616 |
| SECOND | `SECOND/2_DEAD.BIN` | defeat/death quotes | 93 |
| SECOND | `BMESS2.BIN` | battle messages | 17,416 |
| THIRD | `THIRD/THIRD.WAR` | game executable | 220 |
| THIRD | `THIRD/3_SCE.BIN` | scenario dialogue | 4,850 |
| THIRD | `THIRD/3_DEAD.BIN` | defeat/death quotes | 156 |
| THIRD | `BMESS3.BIN` | battle messages | 16,290 |

These numbers deliberately count only records that contain at least four
glyphs and glyph `0x03E` followed later by `0x03F` (`「...」`).  They are a
repeatable high-confidence starting set, not proof that unquoted narration,
names, choices, and UI labels do not exist.

## Renderer bytecode

The EX renderer parses one token at a time as follows:

```text
byte < 0xEB:
    glyph_index = byte

0xEB <= byte < 0xF6:
    glyph_index = ((byte - 0xEB) << 8) | next_byte

0xF6 <= byte < 0xFF:
    renderer control opcode

byte == 0xFF:
    end of record
```

This produces indices `0x000..0xAFF`, exactly matching the `0xB00` embedded
font cells.  Confirmed useful glyphs are:

| Glyph index | Meaning |
| ---: | --- |
| `0x000` | blank/full-width space |
| `0x03E` | `「` |
| `0x03F` | `」` |

The reviewed font table maps 2,309 of 2,366 nonblank glyphs.  It is still a
reviewed first pass, so extraction keeps glyph indices and raw bytes as the
authority and exposes Unicode text only as a derived field.

### Control-code operand lengths

The operand lengths below were followed through the EX text dispatcher.  The
parser entry is at RAM `0x8006AAA8`, and the one/two-byte glyph-index decode is
at `0x8006AB30` and following.

| Opcode | Operand bytes | EX handler RAM | Established meaning |
| ---: | ---: | ---: | --- |
| `F6` | 0 | `0x80069C54` | line advance/newline |
| `F7` | 0 | `0x80069CCC` | page/continue break |
| `F8` | 1 | `0x80069D60` | renderer state; exact semantic unresolved |
| `F9` | 1 | `0x80069F30` | renderer state; exact semantic unresolved |
| `FA` | 0 | `0x80069FEC` | renderer state; exact semantic unresolved |
| `FB` | 2 | `0x8006A088` | renderer state; exact semantic unresolved |
| `FC` | 2 | `0x8006A320` | renderer state/position; exact semantic unresolved |
| `FD` | 2 | `0x8006A38C` | renderer state/position; exact semantic unresolved |
| `FE` | 1 | `0x8006A4AC` | renderer state; exact semantic unresolved |
| `FF` | 0 | `0x8006A574` | terminate/return from text record |

Operand bytes must be skipped as operands even if their values look like
glyphs or `FF`.  This is essential for stable record boundaries.

## Common relative-pointer table

All nine SCE/DEAD/BMESS files begin with the same pointer-table convention.
The first little-endian `u32` is the table's byte length.  It is also the
stored value of pointer zero.  Every entry is relative to its own field:

```text
pointer_count = u32(file + 0) / 4
pointer_field_offset(i) = i * 4
absolute_target(i) = u32(file + i * 4) + i * 4
```

Applying the field offset makes the targets monotonic.  Treating the entries
as ordinary absolute offsets does not.

| File group | Table bytes | Pointer count | First absolute target |
| --- | ---: | ---: | ---: |
| `E_SCE.BIN` | `0x240` | 144 | `0x240` |
| `2_SCE.BIN` | `0x198` | 102 | `0x198` |
| `3_SCE.BIN` | `0x238` | 142 | `0x238` |
| `BMESS2/3/4.BIN` | `0x640` | 400 | `0x640` |
| `E/2/3_DEAD.BIN` | `0x7FC` | 511 | `0x7FC` |

### SCE scenario layout

SCE targets occur in pairs:

```text
target[2*n]     = scenario block start
target[2*n + 1] = meaningful end of that block's text pool

text_pool_start = scenario_block_start + u32(scenario_block_start)
text_pool_end   = target[2*n + 1]
```

The next even target is the next aligned scenario block.  Every observed even
target has this header signature:

```text
u32(block + 0x04) = 0x34
u32(block + 0x0C) = 0x2C
u32(block + 0x18) = 0x1DC
u32(block + 0x1C) = 0x1C
```

This yields 72 EX, 51 SECOND, and 71 THIRD scenario pools.  The final pointer
in each SCE file equals EOF.

### BMESS battle-message layout

Adjacent absolute targets delimit blocks:

```text
block[i] = [target[i], target[i + 1])
```

There are 399 intervals.  Every observed nonempty interval begins with bytes
`43 50 45 01` (`CPE\x01`), followed by compiled records and directly embedded
text bytecode.  This magic is recorded as a block signature only; it is not
evidence that the strings need CPE decompression.  Direct token parsing works
inside each block.

BMESS2 and BMESS3 have 399 nonempty intervals.  BMESS4 has one repeated target
at the beginning and therefore 398 nonempty intervals.  The last target in
all three files equals EOF.

### DEAD quote layout

The 511 targets contain many duplicates, so empty slots are normal.  Scan each
nonempty `[target[i], target[i+1])` interval.  Unlike SCE/BMESS, the final
target is before EOF, so the final interval must end at file size rather than
being discarded.

## Executable-resident messages

The executable files mix code, tables, and text.  They do contain direct
`FF`-terminated renderer records, but there is no single top-level pointer
table proven for the whole executable.  The extractor therefore scans them as
raw regions, accepts records no longer than 512 bytes, and labels even quoted
matches `heuristic_bracketed`.

A representative shared byte sequence begins at these shifted positions:

| Executable | Representative position |
| --- | ---: |
| `SLPS_020.70` | `0x5D60` |
| `TR.WAR` | `0x5E2A` |
| `EX/EX.WAR` | `0x5E5A` |
| `SECOND/SECOND.WAR` | `0x5DC4` |
| `THIRD/THIRD.WAR` | `0x5DC4` |

In the launcher, the surrounding common region is approximately
`0x5D1C..0x6A06`.  Other executable candidates should remain heuristic until
their callers/pointer tables are individually proven.

## Lossless JSON design

Each v2 candidate preserves enough information to reconstruct and later patch
a record without treating the Unicode rendering as authoritative:

- source game, role, path, archive format, and SHA-256;
- absolute start and exclusive end offsets, in decimal and hexadecimal;
- archive slot, region range, scenario index/block start, or CPE marker;
- raw bytes including the `FF` terminator;
- ordered glyph indices;
- ordered tokens with per-token offsets, raw bytes, opcode, and operand bytes;
- stable `source_path@offset` occurrence ID and a raw-record SHA-256;
- derived `japanese_text`, per-record mapping confidence counts, and explicit
  `⟦G:XYZ⟧` placeholders for any unmapped glyph;
- `F6` as a line feed, glyph `0x000` as U+3000 spacing, and remaining controls
  as `⟦F8:AA⟧`-style tags; the zero-operand page break is simply `⟦F7⟧`.

The source summary separately records pointer counts, block counts, candidate
counts, and structural warnings.  `research/dialogue_japanese.sample.json`
contains five candidates from every source.  The compact full result is
`research/dialogue_japanese.full.json` (68,897 candidates); 68,770 records have
no null mapping, while the 127 partial results are overwhelmingly executable
scan false positives.

## Reproduction

From `korean_patch`:

```powershell
# Full extraction with reviewed Japanese text (large compact JSON)
python .\tools\extract_dialogue_candidates.py `
  .\extracted `
  --glyph-map .\research\srwcb_embedded_font_mapping_reviewed.json `
  --compact `
  --output .\research\dialogue_japanese.full.json

# Small diagnostic run; counts still cover every candidate
python .\tools\extract_dialogue_candidates.py `
  .\extracted `
  --glyph-map .\research\srwcb_embedded_font_mapping_reviewed.json `
  --sample-per-source 5 `
  --output .\research\dialogue_japanese.sample.json

# Only pointer-delimited SCE/DEAD/BMESS records
python .\tools\extract_dialogue_candidates.py `
  .\extracted `
  --glyph-map .\research\srwcb_embedded_font_mapping_reviewed.json `
  --archives-only `
  --compact `
  --output .\research\dialogue_japanese.archives.json

# Broader, noisier pass that also retains unquoted records
python .\tools\extract_dialogue_candidates.py `
  .\extracted `
  --include-unbracketed `
  --output .\research\dialogue_candidates.unbracketed.json
```

The extraction step is read-only with respect to the original game files.
Only the explicitly requested JSON output is written.

## Remaining work before reinsertion

1. Continue human review of low/medium OCR rows as translation reaches them;
   never derive missing entries from SJIS order.
2. Separate non-dialogue metadata that happens to survive the bracket filter,
   especially in executables.
3. Decide how to group duplicate battle lines and assign Korean translation
   units without losing per-occurrence offsets or control tokens.
4. When record sizes change, rebuild relative pointer values as
   `new_target - pointer_field_offset`; executable-resident strings may also
   require caller/reference updates or relocation.
