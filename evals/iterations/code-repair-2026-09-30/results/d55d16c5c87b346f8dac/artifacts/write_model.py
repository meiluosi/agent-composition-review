import json
from pathlib import Path


state = {'count': {'type': 'number'}, 'label': {'type': 'string'},
         'left': {'type': 'number'}, 'right': {'type': 'number'}}


def prefixed(prefix, fields):
    return {prefix + '.' + key: value for key, value in fields.items()}


def same(identifier, output, source):
    return {'id': identifier, 'requirement': f'{output} = {source}',
            'must': {'op': 'eq', 'args': [{'path': output}, {'path': source}]}}


def action(identifier, meaning, input_type, output_type, reads, writes, post=None):
    return dict(id=identifier, meaning=meaning, input_type=input_type,
                output_type=output_type, reads=reads, writes=writes,
                pre=[], post=post or [])


def edge(source, target, pairs):
    return {'from': source, 'to': target,
            'bindings': [{'output': a, 'input': b} for a, b in pairs]}


analysis = {key: state[key] for key in ('left', 'right')}
model = {
    'schema_version': 1,
    'types': {
        'State': state,
        'CountPatch': {'count': state['count']},
        'LabelPatch': {'label': state['label']},
        'LeftPatch': {'left': state['left']},
        'RightPatch': {'right': state['right']},
        'AnalysisOutput': analysis,
        'PrepareMerge': {**prefixed('base', state), 'patch.count': state['count']},
        'FinalMerge': {**prefixed('prepared', state), 'refresh.label': state['label'],
                       **prefixed('analysis', analysis)},
    },
    'resources': {
        **{f'parent.{key}': f'父图 State.{key} 通道' for key in state},
        **{f'child.{key}': f'子图 State.{key} 通道；输入为 prepare 提交后的快照'
           for key in state},
        'calls': '可选调用方日志；每个动作入口追加名称',
        'parent_gate': '可选屏障，由 refresh 与 left 等待',
        'child_gate': '可选屏障，由 left 与 right 等待',
    },
    'actions': [
        action('prepare', 'build_graph.prepare：记录动作并返回 count 增量更新后的值',
               'State', 'CountPatch', ['parent.count'], ['parent.count', 'calls']),
        action('prepared_snapshot',
               '父图提交 prepare 的局部更新并为两个后继提供同一步快照；框架边界，非新增 Python 函数',
               'PrepareMerge', 'State', [f'parent.{key}' for key in state],
               [f'child.{key}' for key in state],
               [same('prepared_count', 'output.count', 'input.patch.count')] +
               [same('preserve_' + key, 'output.' + key, 'input.base.' + key)
                for key in ('label', 'left', 'right')]),
        action('refresh', 'build_graph.refresh：等待可选父屏障并给原 label 追加 |fresh',
               'State', 'LabelPatch', ['parent.label', 'parent_gate'],
               ['parent.label', 'parent_gate', 'calls']),
        action('left', 'build_graph.left：在原 left 上累加 prepared count 和旧 label 的长度',
               'State', 'LeftPatch', ['child.left', 'child.count', 'child.label',
                                       'parent_gate', 'child_gate'],
               ['child.left', 'parent_gate', 'child_gate', 'calls']),
        action('right', 'build_graph.right：在原 right 上累加 prepared count 减旧 label 的长度',
               'State', 'RightPatch', ['child.right', 'child.count', 'child.label',
                                        'child_gate'],
               ['child.right', 'child_gate', 'calls']),
        action('analysis_publish',
               'child.compile() 的输出边界：以 AnalysisOutput 仅发布 left/right',
               'AnalysisOutput', 'AnalysisOutput', ['child.left', 'child.right'],
               ['parent.left', 'parent.right'],
               [same('publish_' + key, 'output.' + key, 'input.' + key)
                for key in analysis]),
        action('final_merge',
               '父图在 refresh 与 analysis 完成后提交不相交更新并返回 State；框架边界',
               'FinalMerge', 'State', [f'parent.{key}' for key in state], [],
               [same('final_count', 'output.count', 'input.prepared.count'),
                same('final_label', 'output.label', 'input.refresh.label')] +
               [same('final_' + key, 'output.' + key, 'input.analysis.' + key)
                for key in analysis]),
    ],
    'edges': [
        edge('prepare', 'prepared_snapshot', [('count', 'patch.count')]),
        *[edge('prepared_snapshot', target, [(key, key) for key in state])
          for target in ('refresh', 'left', 'right')],
        edge('left', 'analysis_publish', [('left', 'left')]),
        edge('right', 'analysis_publish', [('right', 'right')]),
        edge('prepared_snapshot', 'final_merge', [(key, 'prepared.' + key) for key in state]),
        edge('refresh', 'final_merge', [('label', 'refresh.label')]),
        edge('analysis_publish', 'final_merge', [(key, 'analysis.' + key) for key in analysis]),
    ],
    'required_actions': ['prepare', 'prepared_snapshot', 'refresh', 'left', 'right',
                         'analysis_publish', 'final_merge'],
    'relations': [
        {'kind': 'input_binding', 'description': 'invoke 的 State 同时供给 prepare 输入和 PrepareMerge.base'},
        {'kind': 'parallel', 'members': ['refresh', 'analysis'],
         'description': '父图 prepare 后分叉；analysis 包含 left、right 和 analysis_publish'},
        {'kind': 'parallel', 'members': ['left', 'right'],
         'description': '子图 START 同时连接 left/right；两者读取相同输入快照'},
        {'kind': 'boundary', 'description': 'prepared_snapshot 到 left/right 的边折叠了 analysis 输入和子图 START 的恒等传递；final_merge 的 prepared 输入是父状态保留值'},
        {'kind': 'barrier', 'members': ['refresh', 'left'], 'resource': 'parent_gate'},
        {'kind': 'barrier', 'members': ['left', 'right'], 'resource': 'child_gate'},
    ],
    'requirements': [
        {'id': 'arithmetic',
         'requirement': '对初始 (c, label, l, r)，最终 count=c+7，label=label+"|fresh"，left=l+c+7+len(label)，right=r+c+7-len(label)。',
         'verification': '直接检查 app.py 的四个动作；test_app.py 的值、重复调用、并发和异步测试使用完整结果断言。加法、减法及字符串拼接未编码为 must 谓词。'},
        {'id': 'single_writer',
         'requirement': '父并行步骤 refresh 只发布 label，analysis 只发布 left/right；count 保留 prepare 的值。',
         'verification': '由输出 schema 和返回字典推断；smoke、屏障及子图独立调用验证。'},
        {'id': 'once_and_isolation',
         'requirement': '正常完成的每次调用四个动作各执行一次；调用之间和不同构建的图之间不泄漏状态。',
         'verification': '测试 Counter 日志、重复调用、同图并发及两个图交错并发；函数没有新增可变缓存。调用方主动共享同一 calls 或 gate 时共享该观察对象。'},
        {'id': 'failures', 'requirement': '节点和屏障异常向调用方传播。',
         'verification': '测试 parent_gate/child_gate 抛出异常和非法 count 引发 TypeError；实现无捕获异常分支。失败调用不保证其他并行分支均完成。'},
    ],
    'scope': {
        'description': '当前 app.py 的父图准备、双层分叉、子图输出投影及最终合并。动作返回 patch；两个 merge 动作抽象 LangGraph 通道提交。State 数值字段实际声明为 int。',
        'limitations': [
            '类型条目是声明而非运行时输入校验；number 无法精确表达 Python int。前提是输入包含 State 的四个合法字段。',
            '空 pre/post 不表示无约束或已证明：四个业务动作的精确算术和字符串要求在 requirements 描述，由代码阅读和行为测试支持，未用谓词表达。',
            '框架内部调度、线程安全实现、检查点、持久化、重试、流式事件和中断恢复未建模或验证。',
            '屏障和并发测试是有限次运行，不能穷尽调度；未验证其他 LangGraph/Python 版本或不满足双参与者协议的自定义 gate。',
            '日志和屏障为调用方可选对象，模型描述其使用点；不保证任意自定义对象的并发安全。',
        ],
        'evidence': [
            {'path': 'app.py', 'start': 6, 'end': 15, 'supports': '完整 State 与仅有 left/right 的 AnalysisOutput'},
            {'path': 'app.py', 'start': 18, 'end': 25, 'supports': '公开接口、可选调用日志及 prepare +7'},
            {'path': 'app.py', 'start': 27, 'end': 45, 'supports': '三条动作公式、原累积值、双层屏障和异常不捕获'},
            {'path': 'app.py', 'start': 47, 'end': 52, 'supports': '子图保留完整 State 输入、限制输出 schema、START 双分支'},
            {'path': 'app.py', 'start': 53, 'end': 62, 'supports': '编译子图嵌入父图，prepare 后双分支和父图编译返回；merge 边界据这些图连接与实测推断'},
        ],
    },
    'validation': {
        'executed': [
            {'command': './run_python smoke.py', 'result': '修复前 InvalidUpdateError(label)；修复后 count=8,label=old|fresh,left=13,right=8'},
            {'command': './run_python test_app.py', 'result': '6 项测试全部通过；包含 3 次双层屏障、40 次两图交错并发和 8 次异步调用'},
        ],
        'inferred': ['输出 schema 避免并行发布旧 label/count；不同构建无内部共享可变状态'],
        'unverified': ['任意调度及框架版本', '检查点、重试及中断恢复'],
    },
}

Path('model.json').write_text(json.dumps(model, ensure_ascii=False, indent=2) + '\n')

# Check references and evidence independently of behavioral tests.
loaded = json.loads(Path('model.json').read_text())
actions = {item['id']: item for item in loaded['actions']}
assert len(actions) == len(loaded['actions'])
assert set(loaded['required_actions']) <= actions.keys()
for item in actions.values():
    assert item['input_type'] in loaded['types']
    assert item['output_type'] in loaded['types']
    assert set(item['reads'] + item['writes']) <= loaded['resources'].keys()
for item in loaded['edges']:
    source, target = actions[item['from']], actions[item['to']]
    for binding in item['bindings']:
        assert binding['output'] in loaded['types'][source['output_type']]
        assert binding['input'] in loaded['types'][target['input_type']]
lines = Path('app.py').read_text().splitlines()
for evidence in loaded['scope']['evidence']:
    assert evidence['path'] == 'app.py'
    assert 1 <= evidence['start'] <= evidence['end'] <= len(lines)
print('model.json written; action/type/resource/binding references and evidence ranges passed')
