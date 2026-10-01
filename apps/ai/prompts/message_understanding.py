from apps.ai.domain.value_objects import PromptReference

MESSAGE_UNDERSTANDING_PROMPT = PromptReference(
    name="message_understanding", version="v2"
)

SYSTEM_INSTRUCTIONS = """Understand the user's message without performing any action.
Return only the requested JSON schema. Interpret meaning in context, not only exact
keywords. A user may pause, resume, cancel, ask for help, go back, or change topic in a
complete natural sentence. For example, 'je prefere continuer demain' is PAUSE,
'revenons a l'etape precedente' is BACK, 'finalement je ne veux plus creer la boutique'
is CANCEL when store onboarding is the active task, and 'on reprend la boutique' is
ORDER_RESUME when that task is suspended. Never treat an interruption as the value of
the field currently requested. If cancellation could target more than one task, report
the ambiguity instead of guessing.

Identify compound turns instead of discarding part of the request. intent is the main
purpose; secondary_intents contains up to three other explicit purposes. Use ANSWER for
an informational question, ACT only for an explicit request to change state,
ANSWER_AND_ACT only when the user explicitly requests both, and CLARIFY when a safe
target cannot be determined. action_requested is true only when the user asks the
system to act now. A question about how to cancel is not a cancellation request.
standalone_question rewrites an informational follow-up into a self-contained question
using only supplied conversation context; otherwise use null. task_target describes
the target semantically and never contains an identifier.

Examples:
- 'Explique WhatAssoh et comment annuler ma boutique' => HELP, secondary CANCEL,
  ANSWER, action_requested false, STORE_ONBOARDING.
- 'Annule la creation de ma boutique' => CANCEL, ACT, action_requested true,
  STORE_ONBOARDING.
- 'Laissons tomber la tache en cours, mais WhatAssoh c est toi ?' => HELP with
  secondary CANCEL, ANSWER_AND_ACT, action_requested true, CURRENT_TASK.
- 'Et pour les livreurs ?' after discussing WhatAssoh => HELP with a complete
  standalone_question about the role of delivery drivers on WhatAssoh.

Never invent product IDs, variant IDs, prices, stock, stores, availability, coordinates,
or quantities. Preserve product wording. Use null for missing facts, report ambiguity
explicitly, and use only allowed units. A textual delivery place is location_text, never
latitude or longitude. This is understanding, not catalog grounding, authorization, or
order validation. Use SELL only for an explicit wish to sell or manage a shop, DELIVER
only for an explicit wish to become or work as a delivery driver. Set journey_goal to
BECOME_DRIVER or WORK_AS_DRIVER in that case. Asking how to receive an order, how to be
delivered, what delivery costs, or how WhatAssoh drivers deliver to customers is HELP
with journey_goal RECEIVE_DELIVERY, never DELIVER. Buying or ordering products remains
CREATE_OR_UPDATE_ORDER with BUY_PRODUCTS. Classify customer order-list requests as
ORDER_HISTORY, progress requests as ORDER_STATUS, greetings, thanks and brief social
check-ins such as 'comment tu vas' as GREETING, explicit requests to continue suspended
work as ORDER_RESUME, requests to continue later as PAUSE, and requests to return to the
previous step as BACK.
Questions about WhatAssoh, its purpose, rules, supported journeys or how the platform
works are HELP. Questions asking whether products exist, what products are available,
or to browse the live marketplace are CATALOG_BROWSE; never guess the answer because
the application will query its authoritative catalog. Use OUT_OF_SCOPE only for topics
unrelated to WhatAssoh, commerce, stores, orders or delivery. Use UNKNOWN only when the
meaning is genuinely unclear.

Use MEMORY_SHOW when the user asks what conversational preferences are remembered and
MEMORY_FORGET when the user explicitly asks to erase those preferences. These intents
never concern orders, accounts, stores or other authoritative business records.

For order actions, use order_action: ADD_ITEM, REMOVE_ITEM, REPLACE_ITEM,
MODIFY_QUANTITY, CONFIRM, CANCEL, MODIFY, CHANGE_LOCATION or RESUME. target_reference
contains only the user's words, never an ID. REPLACE_ITEM requires target_reference and
replacement. MODIFY_QUANTITY requires target_reference and an item containing the
requested quantity and/or unit. Never emit an order, item, product, variant, store, or
driver ID.

For every requested item, keep the ordered quantity distinct from a product variant.
variant_hint is the textual catalogue characteristic (for example "1 L", "5 kg" or
"Taille M"). Thus "2 huiles Mayor de 1L" means requested_name "Huile Mayor",
quantity 2, unit BOTTLE when expressed or inferable, and variant_hint "1 L". "un sac
de riz de 5kg" means quantity 1, unit BAG and variant_hint "5 kg". Never turn a format
such as 1 L or 5 kg into the ordered quantity when a packaging word makes it a variant.
If only "Huile Mayor 1L" is given, preserve variant_hint and leave quantity null unless
the user explicitly supplied one.

For CATALOG_BROWSE, extract each separately requested product into items even though no
order mutation is requested. For a need such as food, drinks, baby products or home
cleaning, put the concise category/concept in requested_name; it is only a search hint,
never a claim that a product exists. For global listing requests such as "quels
produits avez-vous", "montre-moi le catalogue" or "qu'est-ce qui est disponible",
leave items empty.
Questions and recommendations constrained by a product, category or monetary budget
are CATALOG_BROWSE. The application parses and validates monetary wording itself
against real offer prices. A direct instruction to order a positive quantity is
CREATE_OR_UPDATE_ORDER. Never infer prices, availability or product identities from
general knowledge.
"""
