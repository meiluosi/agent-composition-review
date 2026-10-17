"""Reproduce langgraph#9276 and capture an ACR106 trace.

Requires langgraph==1.2.14. Run from this directory:

    python repro.py
    acr trace trace.jsonl

The first stream captures the completed sibling + interrupted gate; the second
stream is the resume. A ``resume`` marker is inserted between the streams
because LangGraph's debug chunks do not emit one.
"""

import json
from pathlib import Path
from typing import TypedDict
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command, interrupt

class State(TypedDict, total=False):
    text: str
    approved: int
    answer: str

def make_graph():
    model = GenericFakeChatModel(messages=iter(AIMessage(content=f"reply {i}") for i in range(1, 20)))
    calls=[]
    def gate(state): return {"approved": interrupt("approve?")}
    def agent(state):
        calls.append(1); return {"answer": model.invoke([HumanMessage(state["text"])]).content}
    block=StateGraph(State)
    block.add_node("gate",gate); block.add_node("agent",agent)
    block.add_edge(START,"gate"); block.add_edge(START,"agent")
    outer=StateGraph(State); outer.add_node("block",block.compile()); outer.add_edge(START,"block")
    return outer.compile(checkpointer=InMemorySaver()),calls

graph,calls=make_graph(); config={"configurable":{"thread_id":"1"}}
raw=[]
def collect(inputs):
    for item in graph.stream(inputs, config, stream_mode="debug", subgraphs=True):
        # item is (namespace tuple, chunk)
        ns, chunk = item if isinstance(item, tuple) and len(item)==2 else ((), item)
        raw.append({"ns": list(ns), "chunk": chunk})
collect({"text":"hello"})
paused=graph.get_state(config); rid=paused.interrupts[0].id
raw.append({"__resume_marker__": True})
collect(Command(resume={rid:1}))
print('calls',len(calls),'raw',len(raw))
events=[]; seq=0
for item in raw:
    if item.get("__resume_marker__"):
        seq+=1; events.append({"id":f"e{seq}","seq":seq,"kind":"resume"}); continue
    c=item["chunk"]; ns=item["ns"]; t=c.get("type"); p=c.get("payload") or {}; step=c.get("step")
    if t=="task":
        seq+=1; events.append({"id":f"e{seq}","seq":seq,"kind":"node_start","node":p.get("name"),"step":step,"args":p.get("input"),"ns":ns})
    elif t=="task_result":
        seq+=1; ev={"id":f"e{seq}","seq":seq,"kind":"node_end","node":p.get("name"),"step":step,"result":p.get("result"),"interrupts":p.get("interrupts"),"ns":ns}
        if p.get("error") is not None: ev["kind"]="error"; ev["message"]=repr(p.get("error"))
        events.append(ev)
out = Path(__file__).with_name("trace.jsonl")
out.write_text('\n'.join(json.dumps(e,ensure_ascii=False) for e in events)+'\n')
print('events',len(events))
for e in events:
    if e.get('kind') in ('node_start','node_end','resume','error'):
        print(e['seq'],e['kind'],e.get('node'),e.get('ns'), 'interrupts' if e.get('interrupts') else '')
