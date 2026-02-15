"""
Claim-level RAG diagnostics (RAGChecker-style).

Provides fine-grained analysis of RAG failures:
- Claim decomposition: Break response into atomic claims
- Claim classification: Identify source (context/parametric/hallucination)
- Lost-in-the-Middle detection: Find facts buried in context middle
- Failure diagnosis: Determine if retrieval or generation failed

Usage:
    from app.evaluation.diagnostics import diagnose_rag_failure

    diagnostic = diagnose_rag_failure(
        query="What is the code blue policy?",
        response="Code Blue activates when...",
        context=["chunk1", "chunk2", "chunk3"],
    )
    print(diagnostic.failure_type)  # "retrieval" | "generation" | "both" | "none"
"""

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional

from dotenv import load_dotenv

# Load environment
env_path = Path(__file__).resolve().parent.parent.parent.parent.parent / ".env"
load_dotenv(env_path)

logger = logging.getLogger(__name__)

# Type aliases
ClaimSource = Literal["context", "parametric", "hallucination"]
ContextPosition = Literal["top", "middle", "bottom", "not_found"]
FailureType = Literal["retrieval", "generation", "both", "none"]


@dataclass
class ClaimClassification:
    """Classification result for a single claim."""

    claim: str
    source: ClaimSource
    relevant: bool
    context_position: ContextPosition
    supporting_context: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RAGDiagnostic:
    """Comprehensive RAG failure diagnostic."""

    query: str
    response: str
    claims: List[str]
    classifications: List[ClaimClassification]
    failure_type: FailureType
    retriever_score: float
    generator_score: float
    hallucination_rate: float
    lost_in_middle_count: int
    context_utilization: float
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "query": self.query,
            "response": (
                self.response[:200] + "..."
                if len(self.response) > 200
                else self.response
            ),
            "claim_count": len(self.claims),
            "failure_type": self.failure_type,
            "retriever_score": round(self.retriever_score, 3),
            "generator_score": round(self.generator_score, 3),
            "hallucination_rate": round(self.hallucination_rate, 3),
            "lost_in_middle_count": self.lost_in_middle_count,
            "context_utilization": round(self.context_utilization, 3),
            "recommendations": self.recommendations,
            "claim_breakdown": {
                "from_context": sum(
                    1 for c in self.classifications if c.source == "context"
                ),
                "parametric": sum(
                    1 for c in self.classifications if c.source == "parametric"
                ),
                "hallucination": sum(
                    1 for c in self.classifications if c.source == "hallucination"
                ),
            },
            "position_breakdown": {
                "top": sum(
                    1 for c in self.classifications if c.context_position == "top"
                ),
                "middle": sum(
                    1 for c in self.classifications if c.context_position == "middle"
                ),
                "bottom": sum(
                    1 for c in self.classifications if c.context_position == "bottom"
                ),
                "not_found": sum(
                    1 for c in self.classifications if c.context_position == "not_found"
                ),
            },
        }


class RAGDiagnostics:
    """
    RAGChecker-style diagnostics for RUSH Policy RAG.

    Performs claim-level analysis to identify:
    1. Which claims are supported by retrieved context
    2. Which claims are from LLM's parametric knowledge
    3. Which claims are hallucinations
    4. "Lost in the Middle" issues where relevant info is buried
    """

    def __init__(
        self,
        azure_endpoint: Optional[str] = None,
        azure_api_key: Optional[str] = None,
        deployment_name: Optional[str] = None,
    ):
        self.azure_endpoint = azure_endpoint or os.getenv("AOAI_ENDPOINT")
        self.azure_api_key = azure_api_key or os.getenv("AOAI_API_KEY")
        # Use gpt-4.1-mini for diagnostics (faster, cheaper for claim analysis)
        self.deployment_name = deployment_name or os.getenv(
            "AOAI_EVAL_DEPLOYMENT", os.getenv("AOAI_CHAT_DEPLOYMENT", "gpt-4.1-mini")
        )

        self._client = None
        self._initialized = False

    def _init_client(self):
        """Initialize Azure OpenAI client for claim analysis."""
        if self._initialized:
            return

        try:
            from openai import AzureOpenAI

            self._client = AzureOpenAI(
                azure_endpoint=self.azure_endpoint,
                api_key=self.azure_api_key,
                api_version="2024-08-01-preview",  # Consistent with metrics.py and conftest.py
            )
            self._initialized = True
            logger.info("RAG Diagnostics initialized with Azure OpenAI")

        except Exception as e:
            logger.error(f"Failed to initialize diagnostics client: {e}")
            raise

    def decompose_to_claims(self, response: str) -> List[str]:
        """
        Break response into atomic claims.

        Uses LLM to extract individual factual claims from the response.
        Each claim should be independently verifiable.

        Args:
            response: The agent's response text

        Returns:
            List of atomic claims
        """
        self._init_client()

        prompt = f"""Extract all factual claims from the following response.
Each claim should be:
1. A single, atomic statement
2. Independently verifiable
3. Not a meta-statement about the response itself

Response:
{response}

Output format: Return ONLY a JSON array of strings, each being one claim.
Example: ["Claim 1 text", "Claim 2 text", "Claim 3 text"]

If the response contains no factual claims, return: []"""

        try:
            completion = self._client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a claim extraction assistant. Extract atomic factual claims from text. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=1000,
            )

            import json

            content = completion.choices[0].message.content.strip()

            # Handle potential markdown code blocks
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            claims = json.loads(content)
            return claims if isinstance(claims, list) else []

        except Exception as e:
            logger.warning(
                f"LLM claim decomposition failed: {e}, falling back to rule-based"
            )
            return self._rule_based_decomposition(response)

    def _rule_based_decomposition(self, response: str) -> List[str]:
        """Fallback rule-based claim extraction."""
        # Split on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+", response)

        claims = []
        for sent in sentences:
            sent = sent.strip()
            # Filter out non-claims
            if len(sent) < 10:
                continue
            if sent.lower().startswith(("i ", "please ", "would you ", "could you ")):
                continue
            if "?" in sent:
                continue
            claims.append(sent)

        return claims

    def classify_claim(
        self,
        claim: str,
        context: List[str],
        query: str,
    ) -> ClaimClassification:
        """
        Classify a single claim by source and position.

        Args:
            claim: The atomic claim to classify
            context: List of retrieved context chunks
            query: The original user query

        Returns:
            ClaimClassification with source, relevance, and position
        """
        self._init_client()

        # Prepare context with position markers
        context_with_positions = []
        for i, chunk in enumerate(context):
            position = self._get_position_label(i, len(context))
            context_with_positions.append(
                f"[{position.upper()} - Chunk {i+1}]\n{chunk}"
            )

        context_text = "\n\n".join(context_with_positions)

        prompt = f"""Analyze whether the following claim is supported by the provided context.

QUERY: {query}

CLAIM TO VERIFY: {claim}

RETRIEVED CONTEXT:
{context_text}

Determine:
1. SOURCE: Is the claim...
   - "context": Directly supported by text in the context
   - "parametric": General knowledge that could be true but not in context
   - "hallucination": Contradicts context or makes unsupported specific claims

2. RELEVANT: Is the claim relevant to answering the query? (true/false)

3. POSITION: If supported by context, which position? (top/middle/bottom/not_found)

4. CONFIDENCE: How confident are you in this classification? (0.0-1.0)

Respond in JSON format ONLY:
{{"source": "context|parametric|hallucination", "relevant": true|false, "position": "top|middle|bottom|not_found", "confidence": 0.0-1.0, "supporting_chunk": "quote if found or null"}}"""

        try:
            completion = self._client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a claim verification assistant. Classify claims accurately. Return only valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=500,
            )

            import json

            content = completion.choices[0].message.content.strip()

            # Handle markdown code blocks
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            result = json.loads(content)

            return ClaimClassification(
                claim=claim,
                source=result.get("source", "hallucination"),
                relevant=result.get("relevant", False),
                context_position=result.get("position", "not_found"),
                supporting_context=result.get("supporting_chunk"),
                confidence=result.get("confidence", 0.5),
            )

        except Exception as e:
            logger.warning(f"LLM claim classification failed: {e}, using heuristic")
            return self._heuristic_classification(claim, context)

    def _heuristic_classification(
        self, claim: str, context: List[str]
    ) -> ClaimClassification:
        """Fallback heuristic-based claim classification."""
        claim_lower = claim.lower()

        # Check each context chunk for support
        for i, chunk in enumerate(context):
            chunk_lower = chunk.lower()

            # Simple substring match
            key_terms = [w for w in claim_lower.split() if len(w) > 4]
            matches = sum(1 for term in key_terms if term in chunk_lower)

            if matches >= len(key_terms) * 0.6:
                return ClaimClassification(
                    claim=claim,
                    source="context",
                    relevant=True,
                    context_position=self._get_position_label(i, len(context)),
                    supporting_context=chunk[:200],
                    confidence=0.5,
                )

        # Not found in context - could be parametric or hallucination
        # Conservative: mark as hallucination if it's specific
        has_specific_detail = any(
            [
                re.search(r"\d+", claim),  # Contains numbers
                re.search(
                    r"policy|procedure|protocol", claim.lower()
                ),  # Policy reference
                re.search(r"must|shall|required", claim.lower()),  # Obligations
            ]
        )

        return ClaimClassification(
            claim=claim,
            source="hallucination" if has_specific_detail else "parametric",
            relevant=True,
            context_position="not_found",
            confidence=0.3,
        )

    def _get_position_label(self, index: int, total: int) -> ContextPosition:
        """Determine position label based on chunk index."""
        if total <= 1:
            return "top"
        if total == 2:
            return "top" if index == 0 else "bottom"

        # Divide into thirds
        third = total / 3
        if index < third:
            return "top"
        elif index < 2 * third:
            return "middle"
        else:
            return "bottom"

    def diagnose(
        self,
        query: str,
        response: str,
        context: List[str],
    ) -> RAGDiagnostic:
        """
        Full RAG failure diagnosis.

        Performs claim-level analysis and determines:
        - Whether the failure is in retrieval, generation, or both
        - Hallucination rate
        - Lost-in-the-middle issues
        - Context utilization

        Args:
            query: User's question
            response: Agent's response
            context: Retrieved context chunks

        Returns:
            RAGDiagnostic with full analysis
        """
        # Decompose response into claims
        claims = self.decompose_to_claims(response)

        if not claims:
            return RAGDiagnostic(
                query=query,
                response=response,
                claims=[],
                classifications=[],
                failure_type="none",
                retriever_score=1.0,
                generator_score=1.0,
                hallucination_rate=0.0,
                lost_in_middle_count=0,
                context_utilization=0.0,
                recommendations=["Response contains no factual claims to verify"],
            )

        # Classify each claim
        classifications = [
            self.classify_claim(claim, context, query) for claim in claims
        ]

        # Calculate metrics
        total_claims = len(classifications)
        from_context = sum(1 for c in classifications if c.source == "context")
        hallucinations = sum(1 for c in classifications if c.source == "hallucination")
        parametric = sum(1 for c in classifications if c.source == "parametric")

        # Lost in the middle: claims from middle positions
        lost_in_middle = sum(
            1
            for c in classifications
            if c.source == "context" and c.context_position == "middle"
        )

        # Calculate scores
        hallucination_rate = hallucinations / total_claims if total_claims > 0 else 0.0
        context_utilization = from_context / total_claims if total_claims > 0 else 0.0

        # Retriever score: How much of the response comes from retrieved context
        retriever_score = (
            (from_context + parametric) / total_claims if total_claims > 0 else 0.0
        )

        # Generator score: How well the generator avoids hallucinations
        generator_score = 1.0 - hallucination_rate

        # Determine failure type
        failure_type = self._determine_failure_type(
            hallucination_rate, context_utilization, from_context, total_claims
        )

        # Generate recommendations
        recommendations = self._generate_recommendations(
            failure_type, hallucination_rate, lost_in_middle, context_utilization
        )

        return RAGDiagnostic(
            query=query,
            response=response,
            claims=claims,
            classifications=classifications,
            failure_type=failure_type,
            retriever_score=retriever_score,
            generator_score=generator_score,
            hallucination_rate=hallucination_rate,
            lost_in_middle_count=lost_in_middle,
            context_utilization=context_utilization,
            recommendations=recommendations,
        )

    def _determine_failure_type(
        self,
        hallucination_rate: float,
        context_utilization: float,
        from_context: int,
        total_claims: int,
    ) -> FailureType:
        """Determine the type of RAG failure."""
        # Thresholds
        HIGH_HALLUCINATION = 0.3
        LOW_CONTEXT_USE = 0.5

        if hallucination_rate > HIGH_HALLUCINATION:
            if context_utilization < LOW_CONTEXT_USE:
                return "both"  # Both retrieval and generation issues
            else:
                return "generation"  # Good retrieval but generator hallucinated
        elif context_utilization < LOW_CONTEXT_USE:
            return "retrieval"  # Retrieved context not used (likely irrelevant)
        else:
            return "none"  # No significant failures

    def _generate_recommendations(
        self,
        failure_type: FailureType,
        hallucination_rate: float,
        lost_in_middle: int,
        context_utilization: float,
    ) -> List[str]:
        """Generate actionable recommendations based on diagnosis."""
        recommendations = []

        if failure_type == "retrieval":
            recommendations.extend(
                [
                    "Improve search query preprocessing (expand synonyms, fix typos)",
                    "Review Azure AI Search semantic configuration",
                    "Consider increasing top_k to retrieve more candidates",
                ]
            )

        if failure_type == "generation":
            recommendations.extend(
                [
                    "Add explicit grounding instructions to system prompt",
                    "Increase temperature penalty for unsupported claims",
                    "Consider Cohere rerank score threshold adjustment",
                ]
            )

        if failure_type == "both":
            recommendations.extend(
                [
                    "Review end-to-end RAG pipeline",
                    "Validate index quality and chunk overlap",
                    "Audit system prompt for grounding requirements",
                ]
            )

        if hallucination_rate > 0.2:
            recommendations.append(
                f"High hallucination rate ({hallucination_rate:.1%}) - strengthen faithfulness constraints"
            )

        if lost_in_middle > 2:
            recommendations.append(
                f"Lost-in-the-Middle detected ({lost_in_middle} claims) - consider reordering context"
            )

        if context_utilization < 0.6:
            recommendations.append(
                f"Low context utilization ({context_utilization:.1%}) - retrieved context may not be relevant"
            )

        if not recommendations:
            recommendations.append(
                "No significant issues detected - RAG pipeline performing well"
            )

        return recommendations


# Singleton instance
_diagnostics: Optional[RAGDiagnostics] = None


def get_diagnostics() -> RAGDiagnostics:
    """Get or create default RAGDiagnostics instance."""
    global _diagnostics
    if _diagnostics is None:
        _diagnostics = RAGDiagnostics()
    return _diagnostics


def decompose_to_claims(response: str) -> List[str]:
    """Convenience function to decompose response into claims."""
    return get_diagnostics().decompose_to_claims(response)


def classify_claim(claim: str, context: List[str], query: str) -> ClaimClassification:
    """Convenience function to classify a single claim."""
    return get_diagnostics().classify_claim(claim, context, query)


def diagnose_rag_failure(
    query: str,
    response: str,
    context: List[str],
) -> RAGDiagnostic:
    """
    Convenience function for full RAG failure diagnosis.

    Args:
        query: User's question
        response: Agent's response
        context: Retrieved context chunks

    Returns:
        RAGDiagnostic with failure type, scores, and recommendations
    """
    return get_diagnostics().diagnose(query, response, context)


if __name__ == "__main__":
    import json

    # Example usage
    diagnostic = diagnose_rag_failure(
        query="What is the code blue policy?",
        response="""According to RUSH Policy RU-123, Code Blue is activated when a patient
        experiences cardiac arrest. The Code Blue team must respond within 3 minutes.
        The team includes a physician, nurses, and respiratory therapist.
        Defibrillation should be attempted within 30 seconds of arrival.""",
        context=[
            "Policy RU-123: Code Blue Protocol. When a patient experiences cardiac arrest, staff must immediately call Code Blue.",
            "The Code Blue response team includes: attending physician, charge nurse, respiratory therapist, and pharmacy representative.",
            "Response time target: The Code Blue team should arrive at the patient's location within 4 minutes of activation.",
        ],
    )

    print(json.dumps(diagnostic.to_dict(), indent=2))
