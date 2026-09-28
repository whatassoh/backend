import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.ai.application.agent_graph import GRAPH_VERSION
from apps.ai.application.runtime import get_ai_runtime_policy


class Command(BaseCommand):
    help = "Display non-secret AI rollout state and optionally verify lot 6."

    def add_arguments(self, parser):
        parser.add_argument(
            "--require-lot6-active",
            action="store_true",
            help="Exit non-zero unless the complete read-only lot 6 profile is active.",
        )

    def handle(self, *args, **options):
        policy = get_ai_runtime_policy()
        status = {
            "graph_version": GRAPH_VERSION,
            "ai_enabled": policy.ai_enabled,
            "emergency_stop": policy.emergency_stop,
            "agent_enabled": policy.agent_enabled,
            "agent_shadow_enabled": policy.agent_shadow_enabled,
            "tools_enabled": policy.tools_enabled,
            "write_tools_enabled": policy.write_tools_enabled,
            "conversation_memory_enabled": policy.conversation_memory_enabled,
            "memory_summary_enabled": policy.memory_summary_enabled,
            "natural_composer_enabled": policy.natural_composer_enabled,
            "style_adaptation_enabled": policy.style_adaptation_enabled,
            "context_engine_enabled": bool(settings.AI_CONTEXT_ENGINE_ENABLED),
            "rag_enabled": policy.rag_enabled,
            "rag_shadow_enabled": policy.rag_shadow_enabled,
            "rag_semantic_enabled": policy.rag_semantic_enabled,
            "rag_rollout_percent": settings.AI_RAG_ROLLOUT_PERCENT,
            "voice_enabled": policy.voice_enabled,
        }
        self.stdout.write(json.dumps(status, sort_keys=True))
        if options["require_lot6_active"]:
            failures = [
                key
                for key, valid in {
                    "ai_enabled": policy.model_calls_enabled,
                    "agent_enabled": policy.agent_enabled,
                    "tools_enabled": policy.tools_enabled,
                    "write_tools_disabled": not policy.write_tools_enabled,
                    "conversation_memory_enabled": policy.conversation_memory_enabled,
                    "memory_summary_enabled": policy.memory_summary_enabled,
                    "natural_composer_enabled": policy.natural_composer_enabled,
                    "style_adaptation_enabled": policy.style_adaptation_enabled,
                    "context_engine_enabled": bool(
                        settings.AI_CONTEXT_ENGINE_ENABLED
                    ),
                    "rag_enabled": policy.rag_enabled,
                    "rag_semantic_enabled": policy.rag_semantic_enabled,
                    "rag_rollout_100": settings.AI_RAG_ROLLOUT_PERCENT == 100,
                    "shadow_disabled": not policy.agent_shadow_enabled
                    and not policy.rag_shadow_enabled,
                }.items()
                if not valid
            ]
            if failures:
                raise CommandError(
                    "Lot 6 active profile is incomplete: " + ", ".join(failures)
                )
            self.stdout.write(self.style.SUCCESS("Lot 6 active profile is complete."))
