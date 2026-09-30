"""Development cases: source origins, user requirements and hidden modeling rubric."""
from pathlib import Path

TASKS=Path(__file__).resolve().parent/'tasks'
REQUIREMENTS={
 'graph':'''审查并在必要时修复 app.py 的 build_graph。保留父图、可复用子图，以及父分支和子分支两处并行关系，仍返回真实 LangGraph 编译图。每次调用 prepare 将 count 加 7；refresh 将 label 加上 |fresh；分析分支用 prepare 之后、refresh 之前的 count 与 label，更新 left += count + len(label)、right += count - len(label)。不得丢掉原始 left/right、改变最终字段、吞掉异常或通过串行化规避问题。多次调用及多个图之间应独立；calls 记录每个动作每次恰好执行一次，gate 是用于观察并行性的可选屏障。仅在违反这些要求时修改实现。''',
 'transaction':'''审查并在必要时修复 app.py 的 write_batch(connection, rows)。rows 是有限的 (key,value) 序列，返回插入数量。此工具使用调用者给的 SQLite 连接，records 表已存在。单批次必须全部成功或全部撤销；失败保留原异常。没有外层事务时成功结果需提交；有外层事务或调用者保存点时，工具不能提交或撤销调用者已有工作，也不能销毁其保存点，调用者以后还能自行提交或回滚。支持默认 sqlite3 连接及 isolation_level=None 下的显式 BEGIN。空批次、重复失败、特殊字符键也遵守相同规则。不关闭或替换调用者的连接。仅在违反要求时修改实现。''',
 'async_group':'''审查并在必要时修复 app.py 的异步 run_group(factories)。factories 是有限的零参数工厂序列，每个返回一个协程，也可能在创建协程时同步抛错。工具需并行执行各子任务，按输入顺序返回结果，空输入返回空列表。任一子任务/工厂失败，或者父调用/子任务取消时，退出之前要取消并等待本次创建的其余任务完成清理；保留原始异常对象或 CancelledError，不把它们当结果、不换成 ExceptionGroup。不能取消无关后台任务。验收覆盖一次外部取消，协程的清理会在有限时间内结束；不要求处理永不响应取消的代码。仅在违反这些要求时修改实现。'''
}
SOURCES={
 'graph':{'kind':'previously_seen_public_issue_derived_development','urls':['https://github.com/langchain-ai/langgraph/issues/6446','https://docs.langchain.com/oss/python/langgraph/use-subgraphs'],'runtime':'langgraph==1.0.3','limitation':'Adaptation of an already used failure family, not new holdout or unmodified upstream reproduction.'},
 'transaction':{'kind':'official_semantics_based_local_reproduction','urls':['https://docs.python.org/3.12/library/sqlite3.html','https://sqlite.org/lang_savepoint.html'],'runtime':'CPython sqlite3 with local SQLite engine','limitation':'Authored application integration error; not a claimed SQLite defect or production incident.'},
 'async_group':{'kind':'official_semantics_based_local_reproduction','urls':['https://docs.python.org/3.12/library/asyncio-task.html'],'runtime':'CPython 3.12 asyncio','limitation':'Authored application integration error; not a claimed asyncio defect or production incident.'}
}
# Reviewer-only semantic criteria. Never copied to the repair agent workspace.
RUBRICS={
 'graph':[
  'Input read scope includes count/label plus existing left/right; child must not lose needed context.',
  'Distinguish child boundary snapshot versus owned-field update; model must match its declared before/after source version.',
  'Represent both parallel relationships or explicitly record their non-sequential nature; do not claim a sequential trace check proves concurrent correctness.'
 ],
 'transaction':[
  'Represent the caller-owned connection/transaction state and per-batch effects on records.',
  'Distinguish standalone commit from nested participation; normal inner return must not finalize outer work.',
  'Represent failure atomicity scoped to this batch and preserve caller work and original error; match declared source version.'
 ],
 'async_group':[
  'Represent tool-group lifetime, concurrent children, ordered results and caller cancellation.',
  'Distinguish propagating an exception from children having finished; account for cleanup before return or explicitly identify its absence in before-model.',
  'Limit cancellation ownership to children of this call; include factory creation failure or acknowledge a modeling gap rather than claiming full coverage.'
 ]
}


def cases():
    result=[]
    for n,(family,source) in enumerate(SOURCES.items()):
        for variant,filename in [('incident','app.py'),('normal','healthy.py')]:
            result.append({'case_id':f'c{len(result)+1:02d}','family':family,'variant':variant,
                           'source_file':str((TASKS/family/filename).relative_to(TASKS.parent.parent.parent)),
                           'request':REQUIREMENTS[family],'provenance':source,'modeling_rubric':RUBRICS[family]})
    return result
