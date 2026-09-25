"""JARVIS Native WhatsApp Contact & Identity Resolver.

Implements Phase 4 contact resolution:
- Normalizes phone numbers (E.164 without +, digits only, 7-15 digits).
- Normalizes and canonicalizes JIDs (@s.whatsapp.net, @lid, @g.us).
- Searches against local contact database with alias precedence.
- NEVER guesses between multiple matching candidates: produces structured candidate
  metadata (display name, phone, JID, alias, business status, recent activity) and
  prompts the operator for disambiguation.
- Preserves unknown LIDs as unresolved instead of guessing or misassigning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from jarvis.execution.whatsapp.store import ContactRecord, WhatsAppDatabaseStore, whatsapp_store
from jarvis.telemetry import logger


@dataclass
class ResolutionResult:
    """Outcome of resolving a natural language target into a WhatsApp recipient."""

    resolved: bool
    ambiguous: bool = False
    canonical_jid: Optional[str] = None
    phone: Optional[str] = None
    display_name: Optional[str] = None
    candidate: Optional[ContactRecord] = None
    candidates: List[ContactRecord] = None  # type: ignore[assignment]
    disambiguation_prompt: Optional[str] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.candidates is None:
            self.candidates = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolved": self.resolved,
            "ambiguous": self.ambiguous,
            "canonical_jid": self.canonical_jid,
            "phone": self.phone,
            "display_name": self.display_name,
            "count": len(self.candidates),
            "disambiguation_prompt": self.disambiguation_prompt,
            "error": self.error,
            "candidates": [
                {
                    "jid": c.jid,
                    "phone": c.phone,
                    "display_name": c.display_name,
                    "full_name": c.full_name,
                    "push_name": c.push_name,
                    "alias": c.alias,
                    "business_name": c.business_name,
                    "tags": c.tags,
                }
                for c in self.candidates
            ],
        }


class JARVISContactResolver:
    """Cognitive contact resolution engine for WhatsApp communication."""

    def __init__(self, store: Optional[WhatsAppDatabaseStore] = None) -> None:
        self.store = store or whatsapp_store

    def normalize_phone(self, raw: str) -> Optional[str]:
        """Normalize phone string to digits-only E.164 format (7-15 digits)."""
        from jarvis.config import settings

        s = raw.strip()
        if s.startswith("+"):
            s = s[1:]
        digits = re.sub(r"\D", "", s)
        if len(digits) == 10 and digits[0] in "6789":
            default_cc = getattr(settings, "WHATSAPP_DEFAULT_COUNTRY_CODE", "91")
            digits = f"{default_cc}{digits}"
        if 7 <= len(digits) <= 15:
            return digits
        return None

    def canonicalize_jid(self, target: str) -> Optional[str]:
        """Convert a phone or JID string into canonical WhatsApp JID format."""
        clean = target.strip()
        if not clean:
            return None

        if "@" in clean:
            user, server = clean.split("@", 1)
            user_clean = user.split(":")[0].strip()
            return f"{user_clean}@{server.strip()}"

        phone = self.normalize_phone(clean)
        if phone:
            return f"{phone}@s.whatsapp.net"

        return None

    def _populate_phone(self, c: ContactRecord) -> ContactRecord:
        """Resolve phone number via LID mapping if missing."""
        if not c.phone and c.jid.endswith("@lid"):
            mapped = self.store.resolve_lid_to_pn(c.jid)
            if mapped:
                c.phone = mapped
        return c

    def _canonical_for_contact(self, c: ContactRecord) -> str:
        """Derive standard phone-based JID for a contact whenever phone is available."""
        if c.phone:
            digits = re.sub(r"\D", "", c.phone)
            if digits:
                return f"{digits}@s.whatsapp.net"
        if c.jid.endswith("@lid"):
            mapped = self.store.resolve_lid_to_pn(c.jid)
            if mapped:
                digits = re.sub(r"\D", "", mapped)
                if digits:
                    return f"{digits}@s.whatsapp.net"
        return c.jid

    def resolve(self, target: str) -> ResolutionResult:
        """Resolve a natural-language target, phone number, or JID.

        If target matches multiple contacts, returns ambiguous=True with a
        disambiguation prompt so JARVIS asks the user instead of guessing.
        """
        clean = target.strip()
        if not clean:
            return ResolutionResult(
                resolved=False,
                error="Target recipient cannot be empty.",
            )

        # 1. Direct JID check
        if "@" in clean:
            canon = self.canonicalize_jid(clean)
            if canon:
                if canon.endswith("@lid"):
                    mapped_pn = self.store.resolve_lid_to_pn(canon)
                    if mapped_pn:
                        pn_jid = f"{mapped_pn}@s.whatsapp.net"
                        existing_pn = self.store.get_contact(pn_jid)
                        if existing_pn:
                            return ResolutionResult(
                                resolved=True,
                                canonical_jid=existing_pn.jid,
                                phone=existing_pn.phone or mapped_pn,
                                display_name=existing_pn.display_name,
                                candidate=existing_pn,
                                candidates=[existing_pn],
                            )
                        return ResolutionResult(
                            resolved=True,
                            canonical_jid=pn_jid,
                            phone=mapped_pn,
                            display_name=mapped_pn,
                        )
                    existing = self.store.get_contact(canon)
                    if existing:
                        return ResolutionResult(
                            resolved=True,
                            canonical_jid=existing.jid,
                            phone=existing.phone,
                            display_name=existing.display_name,
                            candidate=existing,
                            candidates=[existing],
                        )
                    existing_chat = self.store.get_chat(canon)
                    if existing_chat:
                        return ResolutionResult(
                            resolved=True,
                            canonical_jid=canon,
                            phone=None,
                            display_name=existing_chat.name or canon,
                        )
                    return ResolutionResult(
                        resolved=False,
                        error=f"Unmapped LID '{canon}' cannot be resolved without contact or chat history.",
                    )

                existing = self.store.get_contact(canon)
                if existing:
                    return ResolutionResult(
                        resolved=True,
                        canonical_jid=existing.jid,
                        phone=existing.phone,
                        display_name=existing.display_name,
                        candidate=existing,
                        candidates=[existing],
                    )
                phone_part = canon.split("@")[0] if "@s.whatsapp.net" in canon else None
                return ResolutionResult(
                    resolved=True,
                    canonical_jid=canon,
                    phone=phone_part,
                    display_name=phone_part or canon,
                )

        # 2. Direct exact Phone Number check
        phone_norm = self.normalize_phone(clean)
        if phone_norm and len(clean.replace(" ", "").replace("-", "")) == len(phone_norm):
            existing = self.store.get_contact(phone_norm)
            if existing:
                return ResolutionResult(
                    resolved=True,
                    canonical_jid=existing.jid,
                    phone=existing.phone or phone_norm,
                    display_name=existing.display_name,
                    candidate=existing,
                    candidates=[existing],
                )
            # Direct valid phone without stored contact
            canon = f"{phone_norm}@s.whatsapp.net"
            return ResolutionResult(
                resolved=True,
                canonical_jid=canon,
                phone=phone_norm,
                display_name=phone_norm,
            )

        # 3. Search contact database by query
        candidates = [self._populate_phone(c) for c in self.store.search_contacts(clean, limit=20)]

        # Check for exact alias match first (aliases have top precedence)
        exact_alias_matches = [
            c for c in candidates if c.alias and c.alias.lower() == clean.lower()
        ]
        if len(exact_alias_matches) == 1:
            best = exact_alias_matches[0]
            return ResolutionResult(
                resolved=True,
                canonical_jid=self._canonical_for_contact(best),
                phone=best.phone,
                display_name=best.display_name,
                candidate=best,
                candidates=[best],
            )

        # Check for exact full name or display name match
        exact_name_matches = [
            c
            for c in candidates
            if (c.full_name and c.full_name.lower() == clean.lower())
            or (c.display_name and c.display_name.lower() == clean.lower())
            or (c.display_name and c.display_name.lower().lstrip("~").strip() == clean.lower())
            or (c.push_name and c.push_name.lower().lstrip("~").strip() == clean.lower())
        ]
        if len(exact_name_matches) == 1:
            best = exact_name_matches[0]
            return ResolutionResult(
                resolved=True,
                canonical_jid=self._canonical_for_contact(best),
                phone=best.phone,
                display_name=best.display_name,
                candidate=best,
                candidates=[best],
            )

        # If exactly one candidate matched overall
        if len(candidates) == 1:
            best = candidates[0]
            return ResolutionResult(
                resolved=True,
                canonical_jid=self._canonical_for_contact(best),
                phone=best.phone,
                display_name=best.display_name,
                candidate=best,
                candidates=[best],
            )

        # If multiple candidates matched: AMBIGUOUS - NEVER GUESS!
        if len(candidates) > 1:
            candidate_descriptions: List[str] = []
            for c in candidates[:5]:
                phone_str = f" ({c.phone})" if c.phone else ""
                candidate_descriptions.append(f"{c.display_name}{phone_str}")

            names_list = ", ".join(candidate_descriptions)
            prompt = (
                f"I found {len(candidates)} contacts matching '{clean}': {names_list}. "
                "Which one would you like to contact?"
            )
            logger.info(
                "Ambiguous contact resolution for '%s': found %d candidates", clean, len(candidates)
            )
            return ResolutionResult(
                resolved=False,
                ambiguous=True,
                candidates=candidates,
                disambiguation_prompt=prompt,
            )

        # 4. Fallback if clean had digits
        if phone_norm:
            canon = f"{phone_norm}@s.whatsapp.net"
            return ResolutionResult(
                resolved=True,
                canonical_jid=canon,
                phone=phone_norm,
                display_name=phone_norm,
            )

        return ResolutionResult(
            resolved=False,
            ambiguous=False,
            error=f"No WhatsApp contact found matching '{clean}'.",
        )


# Global contact resolver instance
jarvis_contact_resolver = JARVISContactResolver()
