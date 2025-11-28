import asyncio

from app.models.schemas import ChatRequest
from app.services.chat_service import ChatService
from app.services.on_your_data_service import OnYourDataResult, OnYourDataReference


class DummySearchIndex:
    index_name = "dummy-index"

    def search(self, *args, **kwargs):  # pragma: no cover - not used in this test
        return []


class DummyOnYourDataService:
    def __init__(self):
        self.is_configured = True
        self.invocations = 0

    async def chat(self, **kwargs):
        self.invocations += 1
        return OnYourDataResult(
            answer="📋 QUICK ANSWER\nUse aseptic technique.\n\n📄 POLICY REFERENCE\nPolicy: Central Line Care\nReference Number: CL-101",
            citations=[
                OnYourDataReference(
                    content="Always use chlorhexidine.",
                    title="Central Line Care",
                    filepath="central-line-care.pdf",
                    reranker_score=3.8,
                )
            ],
            intent="infection-control",
            raw_response={"mock": True},
        )


def test_chat_service_uses_on_your_data_when_available():
    search_index = DummySearchIndex()
    on_your_data = DummyOnYourDataService()

    service = ChatService(
        search_index=search_index,
        on_your_data_service=on_your_data,
    )

    response = asyncio.run(service.process_chat(ChatRequest(message="How do we clean central lines?")))

    assert response.found is True
    assert response.summary.startswith("Use aseptic technique")
    assert response.evidence
    assert response.evidence[0].title == "Central Line Care"
    assert on_your_data.invocations == 1
