"""
workflows.py — Auto workflow builder for agora-code.

Takes a RouteCatalog (from any scan — backend, frontend fetch calls,
microservices — doesn't matter), asks an LLM to detect which routes
form logical sequences, and generates executable Agora AsyncFlow instances.

Three phases:
  1. detect_workflows(catalog)  → LLM analyzes routes, returns WorkflowCatalog
  2. build_flow(workflow_def)   → creates runnable Agora AsyncFlow + Python code
  3. WorkflowCatalog.to_json()  → persist/restore workflow definitions

Example — Instacart-like repo:
  Routes found: POST /cart/add, POST /cart/checkout, GET /stores, GET /products/search
  LLM detects:  "grocery shopping" = search → add×N → checkout
  Output: AsyncBatchFlow that takes [items, store_name] and loops add → checkout

Works on:
  - Backend routes (FastAPI, Flask, Express, etc.)
  - Frontend API calls (React fetch/axios — if scanned with use_llm=True)
  - Microservice endpoints
  - Any mix of the above
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agora_code.models import Route, RouteCatalog
from agora_code.extractors.llm import _detect_provider, _get_llm, DEFAULT_MODELS


# --------------------------------------------------------------------------- #
#  Data models                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class WorkflowStep:
    """One step in a workflow — maps to one API route."""
    route_method: str          # e.g. "POST"
    route_path: str            # e.g. "/cart/add"
    description: str           # what this step does
    input_mapping: Dict[str, str] = field(default_factory=dict)
    # Maps workflow input fields → route params
    # e.g. {"item_name": "product_name", "qty": "quantity"}
    repeat: bool = False       # True if this step loops (add_to_cart × N)
    repeat_over: Optional[str] = None  # field in shared state to iterate over


@dataclass
class WorkflowDef:
    """
    A named workflow — a logical sequence of API calls.

    Detected automatically by the LLM from route patterns,
    or defined manually.
    """
    name: str                              # e.g. "grocery_shopping"
    description: str                       # human-readable explanation
    steps: List[WorkflowStep]              # ordered list of API calls
    input_schema: Dict[str, Any] = field(default_factory=dict)
    # JSON-schema style: {"items": {"type": "array"}, "store": {"type": "string"}}
    trigger_keywords: List[str] = field(default_factory=list)
    # Keywords that suggest this workflow: ["buy", "order", "add to cart"]
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [
                {
                    "route_method": s.route_method,
                    "route_path": s.route_path,
                    "description": s.description,
                    "input_mapping": s.input_mapping,
                    "repeat": s.repeat,
                    "repeat_over": s.repeat_over,
                }
                for s in self.steps
            ],
            "input_schema": self.input_schema,
            "trigger_keywords": self.trigger_keywords,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowDef":
        steps = [WorkflowStep(**s) for s in data.get("steps", [])]
        return cls(
            name=data["name"],
            description=data["description"],
            steps=steps,
            input_schema=data.get("input_schema", {}),
            trigger_keywords=data.get("trigger_keywords", []),
            tags=data.get("tags", []),
        )


@dataclass
class WorkflowCatalog:
    """All detected workflows for a scanned repo."""
    source: str                            # repo path or URL
    workflows: List[WorkflowDef]

    def to_json(self) -> str:
        return json.dumps({
            "source": self.source,
            "workflows": [w.to_dict() for w in self.workflows],
        }, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "WorkflowCatalog":
        data = json.loads(raw)
        return cls(
            source=data["source"],
            workflows=[WorkflowDef.from_dict(w) for w in data["workflows"]],
        )

    def __len__(self) -> int:
        return len(self.workflows)

    def get(self, name: str) -> Optional[WorkflowDef]:
        return next((w for w in self.workflows if w.name == name), None)


# --------------------------------------------------------------------------- #
#  Phase 1: LLM sequence detection                                            #
# --------------------------------------------------------------------------- #

_WORKFLOW_SYSTEM_PROMPT = """\
You are an API workflow analyst.

Given a list of API routes, identify groups of routes that are typically
called together in sequence to accomplish a user goal.

Return a JSON object in exactly this format:
{
  "workflows": [
    {
      "name": "snake_case_workflow_name",
      "description": "What this workflow accomplishes for the user",
      "trigger_keywords": ["buy", "order", "purchase"],
      "tags": ["e-commerce", "cart"],
      "input_schema": {
        "items": {"type": "array", "description": "List of items to process"},
        "store_name": {"type": "string", "description": "Target store"}
      },
      "steps": [
        {
          "route_method": "GET",
          "route_path": "/products/search",
          "description": "Search for each item",
          "input_mapping": {"query": "item_name"},
          "repeat": true,
          "repeat_over": "items"
        },
        {
          "route_method": "POST",
          "route_path": "/cart/add",
          "description": "Add each item to cart",
          "input_mapping": {"product_id": "found_product_id", "quantity": "qty"},
          "repeat": true,
          "repeat_over": "items"
        },
        {
          "route_method": "POST",
          "route_path": "/cart/checkout",
          "description": "Complete the purchase",
          "input_mapping": {},
          "repeat": false,
          "repeat_over": null
        }
      ]
    }
  ]
}

Rules:
- Only create workflows where 2+ routes logically work together
- repeat=true means this step runs once per item in repeat_over list
- input_mapping maps workflow input field names to route param names
- trigger_keywords are natural language phrases that suggest this workflow
- If no clear sequences exist, return {"workflows": []}
- Return ONLY the JSON, no explanation
"""


async def detect_workflows(
    catalog: RouteCatalog,
    provider: str = "auto",
    model: Optional[str] = None,
) -> WorkflowCatalog:
    """
    Phase 1: Ask LLM to detect workflow sequences from a RouteCatalog.

    Args:
        catalog:  RouteCatalog from agora-code scan
        provider: "auto" | "claude" | "openai" | "gemini"
        model:    override default model

    Returns:
        WorkflowCatalog with all detected workflows
    """
    if not catalog.routes:
        return WorkflowCatalog(source=catalog.source, workflows=[])

    # Resolve provider
    if provider in ("auto", "", None):
        detected_provider, detected_model = _detect_provider()
        if not detected_provider:
            raise RuntimeError(
                "No LLM provider for workflow detection. Set ANTHROPIC_API_KEY, "
                "OPENAI_API_KEY, or GEMINI_API_KEY."
            )
        provider = detected_provider
        model = model or detected_model
    else:
        model = model or DEFAULT_MODELS.get(provider)

    # Build route summary for the LLM
    route_lines = []
    for r in catalog.routes:
        params = ", ".join(
            f"{p.name}:{p.type}{'*' if p.required else ''}"
            for p in r.params
        )
        desc = r.description[:60] if r.description else ""
        route_lines.append(
            f"{r.method} {r.path}"
            + (f"  [{params}]" if params else "")
            + (f"  # {desc}" if desc else "")
        )

    routes_text = "\n".join(route_lines)
    prompt = (
        f"Here are the API routes for: {catalog.source}\n\n"
        f"{routes_text}\n\n"
        "Identify workflow sequences from these routes."
    )

    # Call LLM (reuse the same backend as Tier 3)
    llm_fn = _make_workflow_llm(provider, model)
    raw = await llm_fn(prompt)
    workflows = _parse_workflows(raw, catalog)

    return WorkflowCatalog(source=catalog.source, workflows=workflows)


# --------------------------------------------------------------------------- #
#  Phase 2: Build executable Agora AsyncFlow                                  #
# --------------------------------------------------------------------------- #

def build_flow(
    workflow: WorkflowDef,
    base_url: str,
    auth: Optional[Dict] = None,
):
    """
    Phase 2: Convert a WorkflowDef into an executable Agora AsyncFlow.

    Requires: pip install agora (the Agora framework)
    If Agora is not installed, returns None and logs a warning.

    Args:
        workflow: WorkflowDef from detect_workflows()
        base_url: e.g. "https://api.example.com"
        auth:     {"type": "bearer", "token": "..."} or None

    Returns:
        Agora AsyncFlow | AsyncBatchFlow instance, or None if Agora not installed
    """
    try:
        from agora import AsyncNode, AsyncFlow, AsyncBatchFlow
    except ImportError:
        import warnings
        warnings.warn(
            "Agora framework not installed — can't build executable flow. "
            "Install with: pip install agora\n"
            "You can still use WorkflowDef.to_dict() to serialize the workflow."
        )
        return None

    from agora_code.agent import APICallNode
    from agora_code.models import Route, RouteCatalog

    has_repeat = any(s.repeat for s in workflow.steps)

    # Build a node for each step
    nodes = []
    for step in workflow.steps:
        # Find matching Route — create a minimal one if not available
        route = Route(
            method=step.route_method,
            path=step.route_path,
            description=step.description,
            params=[],
        )

        # Wrap APICallNode in an AsyncNode that handles input mapping
        class _StepNode(AsyncNode):
            def __init__(self, _route, _base_url, _auth, _step):
                super().__init__(name=f"{_step.route_method}_{_step.route_path.replace('/', '_')}")
                self._api_node = APICallNode(
                    route=_route,
                    base_url=_base_url,
                    auth=_auth or {},
                )
                self._step = _step

            async def prep_async(self, shared):
                # Apply input_mapping: pull fields from shared state
                args = {}
                for route_param, workflow_key in self._step.input_mapping.items():
                    if workflow_key in shared:
                        args[route_param] = shared[workflow_key]
                    elif "item" in shared and workflow_key in shared.get("item", {}):
                        args[route_param] = shared["item"][workflow_key]
                return args

            async def exec_async(self, prep_res):
                result, _ = await self._api_node.run(prep_res)
                return result

            async def post_async(self, shared, prep_res, exec_res):
                shared[f"result_{self._step.route_path.replace('/', '_')}"] = exec_res
                return "default"

        node = _StepNode(route, base_url, auth, step)
        nodes.append(node)

    if not nodes:
        return None

    # Chain nodes together using >>
    for i in range(len(nodes) - 1):
        nodes[i] >> nodes[i + 1]

    if has_repeat:
        flow = AsyncBatchFlow(name=workflow.name, start=nodes[0])
    else:
        flow = AsyncFlow(name=workflow.name, start=nodes[0])

    return flow


def generate_flow_code(workflow: WorkflowDef, base_url: str) -> str:
    """
    Generate Python source code for this workflow (for saving/deploying).

    Returns a self-contained Python file string that defines the workflow
    as an Agora AsyncFlow. Users can review, edit, and run this directly.
    """
    lines = [
        "# Auto-generated by agora-code workflow builder",
        "# Review and edit before deploying.",
        "",
        "import asyncio",
        "from agora import AsyncNode, AsyncFlow, AsyncBatchFlow",
        "from agora_code.agent import APICallNode",
        "from agora_code.models import Route",
        "",
        f"BASE_URL = {base_url!r}",
        "",
        f'# Workflow: {workflow.name}',
        f'# {workflow.description}',
        f'# Triggers: {", ".join(workflow.trigger_keywords)}',
        "",
    ]

    # Generate a node class per step
    for i, step in enumerate(workflow.steps):
        class_name = f"Step{i+1}_{step.route_method}_{step.route_path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
        lines += [
            f"class {class_name}(AsyncNode):",
            f"    \"\"\"Step {i+1}: {step.description}\"\"\"",
            f"",
            f"    async def prep_async(self, shared):",
            f"        return {{",
        ]
        for route_param, wf_key in step.input_mapping.items():
            lines.append(f"            {route_param!r}: shared.get({wf_key!r}),")
        lines += [
            f"        }}",
            f"",
            f"    async def exec_async(self, args):",
            f"        route = Route(method={step.route_method!r}, path={step.route_path!r}, params=[])",
            f"        node = APICallNode(route=route, base_url=BASE_URL)",
            f"        result, _ = await node.run(args)",
            f"        return result",
            f"",
            f"    async def post_async(self, shared, prep_res, exec_res):",
            f"        shared['last_result'] = exec_res",
            f"        return 'default'",
            f"",
            f"",
        ]

    # Wire them together
    node_vars = [
        f"step{i+1}" for i in range(len(workflow.steps))
    ]
    step_classes = [
        f"Step{i+1}_{s.route_method}_{s.route_path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
        for i, s in enumerate(workflow.steps)
    ]

    lines.append("# Build the flow")
    for var, cls in zip(node_vars, step_classes):
        lines.append(f"{var} = {cls}()")

    if len(node_vars) > 1:
        chain = " >> ".join(node_vars)
        lines.append(f"{chain}")

    has_repeat = any(s.repeat for s in workflow.steps)
    flow_cls = "AsyncBatchFlow" if has_repeat else "AsyncFlow"
    lines += [
        f"",
        f"flow = {flow_cls}(name={workflow.name!r}, start={node_vars[0]})",
        f"",
        f"",
        f"async def run(shared: dict):",
        f"    \"\"\"Run the {workflow.name} workflow.\"\"\"",
        f"    await flow.run_async(shared)",
        f"    return shared.get('last_result')",
        f"",
        f"",
        f"if __name__ == '__main__':",
        f"    # Example:",
        f"    shared = {json.dumps({k: f'<{k}>' for k in workflow.input_schema}, indent=8)}",
        f"    asyncio.run(run(shared))",
    ]

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  LLM backend (reuses Tier 3 infrastructure)                                 #
# --------------------------------------------------------------------------- #

def _make_workflow_llm(provider: str, model: str):
    """Return an async function that calls the LLM with the workflow system prompt."""

    async def call(prompt: str) -> str:
        fn = _get_llm(provider, model)

        # We need to pass system+user separately for each provider
        # Reuse _get_llm but override system prompt
        if provider in ("claude", "anthropic"):
            try:
                import anthropic
                client = anthropic.AsyncAnthropic()
                resp = await client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=_WORKFLOW_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text if resp.content else "{}"
            except ImportError:
                pass

        if provider == "openai":
            try:
                from openai import AsyncOpenAI
                client = AsyncOpenAI()
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": _WORKFLOW_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                return resp.choices[0].message.content or "{}"
            except ImportError:
                pass

        if provider == "gemini":
            try:
                import google.generativeai as genai
                gen_model = genai.GenerativeModel(
                    model_name=model,
                    system_instruction=_WORKFLOW_SYSTEM_PROMPT,
                    generation_config={"response_mime_type": "application/json"},
                )
                resp = await gen_model.generate_content_async(prompt)
                return resp.text or "{}"
            except ImportError:
                pass

        return "{}"

    return call


# --------------------------------------------------------------------------- #
#  Output parsing                                                              #
# --------------------------------------------------------------------------- #

def _parse_workflows(raw: str, catalog: RouteCatalog) -> List[WorkflowDef]:
    """Parse LLM JSON output into WorkflowDef list. Validates route references."""
    try:
        data = json.loads(raw)
        raw_workflows = data.get("workflows", [])
    except (json.JSONDecodeError, AttributeError):
        return []

    # Build a set of valid route keys for validation
    valid_routes = {
        f"{r.method.upper()} {r.path}" for r in catalog.routes
    }

    result = []
    for wf in raw_workflows:
        try:
            steps = []
            for s in wf.get("steps", []):
                method = s.get("route_method", "").upper()
                path = s.get("route_path", "")
                # Accept even if LLM hallucinated a slightly off path
                steps.append(WorkflowStep(
                    route_method=method,
                    route_path=path,
                    description=s.get("description", ""),
                    input_mapping=s.get("input_mapping", {}),
                    repeat=s.get("repeat", False),
                    repeat_over=s.get("repeat_over"),
                ))
            if len(steps) >= 2:  # Only keep multi-step workflows
                result.append(WorkflowDef(
                    name=wf.get("name", "workflow"),
                    description=wf.get("description", ""),
                    steps=steps,
                    input_schema=wf.get("input_schema", {}),
                    trigger_keywords=wf.get("trigger_keywords", []),
                    tags=wf.get("tags", []),
                ))
        except Exception:
            continue

    return result
