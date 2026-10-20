"""The two library prompts and every version setup.py saves.

support-answer is the support agent's answer step from the agent tracing demo, moved out of the
code. Its system text has two rules a check can test: state the order number, and never promise
a refund date. Each later version is one realistic change:

  v1  the baseline, on GPT-6 Luna
  v2  shorter system text that drops the order-number rule (the regression the evals catch)
  v3  v1's text on DeepSeek V4.1 Flash instead (the cost change the rollout tries)
  v4  v1 plus a {{tone}} variable with no default (the change that breaks today's callers)

policy-check is the sub-agent's check: a JSON answer that follows a schema.

A version's params take temperature, max_tokens, top_p and response_format, each as a string.
An output schema is kept only with response_format set to "json_schema" and the schema itself in
the body's schema field; with any other response_format the schema is dropped.

Since a gateway fix in October 2026 a version can also store reasoning_effort, applied on Chat
Completions and Responses but not on Messages. These versions were written before that and do
not store it, so a caller that wants less reasoning sends reasoning_effort with the call.
"""

ANSWER_SYSTEM = (
    "You are a support agent for an online shop that sells {{product}}. Answer the customer in at most "
    "three sentences, plainly and specifically. Always state the order number when the customer gives one. "
    "Never promise a refund date or timeframe: say instead that the refund goes to the original payment "
    "method once the return has been checked."
)
ANSWER_SYSTEM_SHORT = (
    "You are a support agent for an online shop that sells {{product}}. Answer in at most three sentences. "
    "Never promise a refund date or timeframe."
)
ANSWER_USER = "Order status: {{order_status}}\n\nCustomer message: {{customer_message}}"
# Generous, because GPT-6 Luna reasons before it answers and the cap counts both.
ANSWER_PARAMS = {"max_tokens": "1500"}

ANSWER_VERSIONS = [
    {"note": "v1: baseline", "body": {
        "system": ANSWER_SYSTEM, "messages": [{"role": "user", "content": ANSWER_USER}],
        "model": "gpt-6-luna", "params": ANSWER_PARAMS, "defaults": {"product": "home electronics"}}},
    {"note": "v2: shorter system text, without the order-number rule", "body": {
        "system": ANSWER_SYSTEM_SHORT, "messages": [{"role": "user", "content": ANSWER_USER}],
        "model": "gpt-6-luna", "params": ANSWER_PARAMS, "defaults": {"product": "home electronics"}}},
    {"note": "v3: v1 on DeepSeek V4.1 Flash", "body": {
        "system": ANSWER_SYSTEM, "messages": [{"role": "user", "content": ANSWER_USER}],
        "model": "deepseek-v4.1-flash", "params": ANSWER_PARAMS,
        "defaults": {"product": "home electronics"}}},
    {"note": "v4: v1 with a tone variable and no default", "body": {
        "system": ANSWER_SYSTEM + " Write in a {{tone}} tone.", "messages": [{"role": "user", "content": ANSWER_USER}],
        "model": "gpt-6-luna", "params": ANSWER_PARAMS, "defaults": {"product": "home electronics"}}},
]

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["topics", "risk"],
    "additionalProperties": False,
}
CHECK_VERSIONS = [
    {"note": "v1: baseline with an output schema", "body": {
        "system": ("You review messages to an online electronics shop's support desk. List the policy topics "
                   "the message touches (returns, refunds, shipping, warranty, billing) and rate how much "
                   "a human should look at it."),
        "messages": [{"role": "user", "content": "{{customer_message}}"}],
        "model": "deepseek-v4.1-flash",
        "params": {"max_tokens": "300", "response_format": "json_schema"},
        "schema": {"name": "policy_check", "strict": True, "schema": CHECK_SCHEMA}}},
]

PROMPTS = [
    {"id": "support-answer", "name": "Support answer", "description": "The support agent's reply to a customer.",
     "versions": ANSWER_VERSIONS, "labels": {"production": 1, "staging": 2}},
    {"id": "policy-check", "name": "Policy check", "description": "The sub-agent's policy and risk check, as JSON.",
     "versions": CHECK_VERSIONS, "labels": {"production": 1}},
]

# The customer messages of the agent tracing demo, and a few more like them.
MESSAGES = [
    ("My order A-48213 arrived but the dock does not charge my laptop. Can I send it back?", "delivered on 2026-09-28"),
    ("Where is order A-50977? It has been a week.", "in transit, shipped on 2026-10-01"),
    ("If it does not arrive by Friday, can I cancel and get a refund?", "in transit, shipped on 2026-10-01"),
    ("How long until I get my money back once you have the return?", "return received on 2026-10-06"),
    ("The webcam I bought has a dead pixel. What are my options?", "delivered on 2026-09-30"),
]
