'use strict';
const assert = require('node:assert/strict'),
    fs = require('node:fs'),
    path = require('node:path'),
    vm = require('node:vm');
const source = fs
    .readFileSync(path.join(__dirname, '../console_web/app.js'), 'utf8')
    .replace(/\ninit\(\);\s*$/, '\n');

function fixture() {
    const elements = new Map();
    function element(id) {
        if (elements.has(id)) return elements.get(id);
        let value = '';
        const el = {
            id,
            type: 'number',
            dataset: {},
            disabled: false,
            hidden: false,
            checked: false,
            handlers: {},
            classList: { add() {}, remove() {}, toggle() {} },
            addEventListener(name, fn) {
                this.handlers[name] = fn;
            },
            querySelectorAll() {
                return [];
            },
            querySelector() {
                return null;
            },
            setAttribute() {},
            replaceChildren() {},
        };
        Object.defineProperty(el, 'value', {
            get() {
                return value;
            },
            set(next) {
                value = String(next);
            },
        });
        elements.set(id, el);
        return el;
    }
    const document = {
        activeElement: null,
        hidden: false,
        getElementById: element,
        addEventListener() {},
        querySelectorAll(selector) {
            const match = selector.match(/^\[data-rect="(train|legal)"\]$/);
            return match ? [0, 1, 2, 3].map((i) => element(match[1] + i)) : [];
        },
    };
    const context = vm.createContext({
        document,
        location: { search: '?connect=manual' },
        URLSearchParams,
        structuredClone,
        crypto: { randomUUID: () => 'test-page' },
        history: { replaceState() {} },
        window: { addEventListener() {} },
        setTimeout: () => 0,
        clearTimeout() {},
        setInterval() {},
        console,
    });
    vm.runInContext(source, context);
    const run = (code) => vm.runInContext(code, context);
    run('refresh=async()=>{};point=(svg,e)=>[e.clientX,e.clientY]');
    function board(confidence = 85, hits = 3, recorded = 100) {
        return {
            connected: true,
            camera: {
                sensor: { exposure_us: 1000, analog_gain_db: 0 },
                automatic: { config: { frozen: true } },
                isp: { config: { rg: 256, bg: 256 }, wb_auto: false },
            },
            runtime: {
                inference: {
                    async_inference: {
                        input_transform: 'native_tiles_v1',
                        model_input_size: [512, 288],
                    },
                    counting: { class_counts: [] },
                },
                results: [],
            },
            displays: {},
            rect: {
                version: 'actual-version',
                recorded_monotonic: recorded,
                settings: {
                    rectangle: [704, 396, 512, 288],
                    confidence_percent: confidence,
                    track_hits: hits,
                },
            },
        };
    }
    context.board = board();
    run('state={jobs:[],board,flow:{}};renderBoard(board)');
    return { context, run, element, board };
}
const cases = [];
function test(name, fn) {
    cases.push([name, fn]);
}

test('first board read displays actual parameters and locks only model width and height', () => {
    const f = fixture();
    assert.equal(f.element('confidence').value, '85');
    assert.equal(f.element('trackHits').value, '3');
    assert.equal(f.element('legal2').value, '512');
    assert.equal(f.element('legal3').value, '288');
    assert.equal(f.element('legal2').disabled, true);
    assert.equal(f.element('legal3').disabled, true);
    assert.equal(f.element('legal0').disabled, false);
    assert.equal(f.element('confidence').disabled, false);
    assert.equal(f.element('trackHits').disabled, false);
    assert.equal(f.element('legal0').max, 1408);
    assert.equal(f.element('legal1').max, 792);
});

test('polls preserve edited fields while synchronizing untouched fields', () => {
    const f = fixture();
    f.element('confidence').value = '77';
    f.element('confidence').oninput();
    f.context.board = f.board(60, 5, 110);
    f.run('renderBoard(board)');
    assert.equal(f.element('confidence').value, '77');
    assert.equal(f.element('trackHits').value, '5');
    assert.equal(f.run("legalDirty.has('confidence')"), true);
});

async function submit(f) {
    f.context.requests = [];
    f.run(
        "api=async(path,body)=>{requests.push({path,body});return{id:'legal-job',kind:'legal',state:'queued'};}",
    );
    await f.run("action('legal',{values:legalValues()},'legal')");
}
function finish(f, state = 'complete', confidence = 68) {
    f.context.finished = {
        id: 'legal-job',
        page: 'test-page',
        kind: 'legal',
        state,
        message: 'offline result',
        result: {
            settings: {
                rectangle: [704, 396, 512, 288],
                confidence_percent: confidence,
                track_hits: 3,
            },
            version: 'applied-version',
            capture_received_monotonic: 120,
        },
    };
    f.run('state.jobs=[finished];renderJobs();renderBoard(state.board)');
}

test('successful control job uses actual acknowledgment and ignores older cached board status', async () => {
    const f = fixture();
    f.element('confidence').value = '70';
    f.element('confidence').oninput();
    await submit(f);
    finish(f);
    assert.equal(f.element('confidence').value, '68');
    assert.equal(f.run("legalDirty.has('confidence')"), false);
    f.context.board = f.board(85, 1, 110);
    f.run('renderBoard(board)');
    assert.equal(f.element('confidence').value, '68');
    assert.equal(f.element('trackHits').value, '3');
    assert.match(f.element('legalStatus').textContent, /置信度 68%/);
    f.context.board = f.board(68, 3, 125);
    f.run('renderBoard(board)');
    assert.equal(f.element('confidence').value, '68');
    assert.equal(f.context.requests[0].body.values.track_hits, 3);
});

test('editing after submission preserves the newer draft, including changed-then-restored values', async () => {
    const f = fixture();
    f.element('confidence').value = '70';
    f.element('confidence').oninput();
    await submit(f);
    f.element('confidence').value = '77';
    f.element('confidence').oninput();
    f.element('confidence').value = '70';
    f.element('confidence').oninput();
    finish(f);
    assert.equal(f.element('confidence').value, '70');
    assert.equal(f.run("legalDirty.has('confidence')"), true);
    f.context.board = f.board(68, 3, 125);
    f.run('renderBoard(board)');
    assert.equal(f.element('confidence').value, '70');
});

test('failed control job keeps the unsubmitted draft', async () => {
    const f = fixture();
    f.element('confidence').value = '77';
    f.element('confidence').oninput();
    await submit(f);
    finish(f, 'error');
    f.context.board = f.board(85, 3, 125);
    f.run('renderBoard(board)');
    assert.equal(f.element('confidence').value, '77');
    assert.equal(f.run("legalDirty.has('confidence')"), true);
});

test('inference drag moves a fixed size region within the frame; training drag still resizes', () => {
    const f = fixture(),
        svg = { setPointerCapture() {} };
    f.context.svg = svg;
    f.run('step=4;liveMeta={camera_area:[0,0,1920,1080]};attachLivePointer(svg)');
    const event = (x, y) => ({
        button: 0,
        clientX: x,
        clientY: y,
        pointerId: 1,
        preventDefault() {},
    });
    svg.onpointerdown(event(800, 450));
    svg.onpointermove(event(2000, 1200));
    svg.onpointerup();
    assert.deepEqual(Array.from(f.run('rects.legal')), [1408, 792, 512, 288]);
    assert.equal(f.element('confidence').value, '85');
    assert.equal(f.element('trackHits').value, '3');
    f.run('step=1');
    svg.onpointerdown(event(100, 100));
    svg.onpointermove(event(900, 600));
    svg.onpointerup();
    assert.deepEqual(Array.from(f.run('rects.train')), [100, 100, 800, 500]);
});

test('control submission rejects an input size change', () => {
    const f = fixture();
    f.run('rects.legal[2]=1024');
    assert.throws(() => f.run('legalValues()'), /输入尺寸/);
});

test('capture selection reports area on numeric edits and preserves native coordinates', () => {
    const f = fixture(),
        inputs = [0, 1, 2, 3].map((i) => {
            const el = f.element('train' + i);
            el.dataset.index = String(i);
            return el;
        });
    f.element('trainRect').querySelectorAll = () => inputs;
    f.run("setupRect('train','trainRect');rects.train=[100,100,960,540];syncRect('train')");
    assert.equal(f.element('trainPixels').textContent, '518,400 / 518,400 像素');
    assert.deepEqual(Array.from(f.run("validRect('train')")), [100, 100, 960, 540]);
    inputs[2].value = '961';
    inputs[2].oninput();
    assert.match(f.element('trainPixels').textContent, /518,940.*超出上限/);
    assert.throws(() => f.run("validRect('train')"), /518,400/);
    assert.deepEqual(Array.from(f.run('rects.train')), [100, 100, 961, 540]);
    f.run('rects.train=[0,0,511,288]');
    assert.throws(() => f.run("validRect('train')"), /至少512×288/);
});

test('oversized drag stays visible for correction and cannot submit a capture request', async () => {
    const f = fixture(),
        svg = { setPointerCapture() {} };
    f.context.svg = svg;
    f.run('step=1;liveMeta={camera_area:[0,0,1920,1080]};attachLivePointer(svg)');
    const event = (x, y) => ({
        button: 0,
        clientX: x,
        clientY: y,
        pointerId: 1,
        preventDefault() {},
    });
    svg.onpointerdown(event(0, 0));
    svg.onpointermove(event(1920, 1080));
    svg.onpointerup();
    assert.deepEqual(Array.from(f.run('rects.train')), [0, 0, 1920, 1080]);
    assert.match(f.element('trainPixels').textContent, /超出上限/);
    f.context.requests = [];
    f.run('api=async(path,body)=>{requests.push({path,body});return{};}');
    await assert.rejects(f.run("action('freeze',{rectangle:rects.train.slice()})"), /518,400/);
    assert.equal(f.context.requests.length, 0);
});

test('capture and test reject oversized saved flows before sending API requests', async () => {
    const f = fixture();
    f.context.requests = [];
    f.run(
        'state.flow.rectangle=[0,0,1920,1080];rects.train=[704,396,512,288];api=async(path,body)=>{requests.push({path,body});return{};}',
    );
    for (const name of ['capture', 'test'])
        await assert.rejects(f.run(`action('${name}')`), /518,400/);
    assert.equal(f.context.requests.length, 0);
    assert.deepEqual(Array.from(f.run('state.flow.rectangle')), [0, 0, 1920, 1080]);
    f.run('state.flow.rectangle=[100,100,960,540]');
    await f.run("action('capture',{append:true})");
    await f.run("action('test',{run:'selected-run'})");
    assert.deepEqual(
        Array.from(f.context.requests, (request) => request.path),
        ['/api/action/capture', '/api/action/test'],
    );
});

test('model switching sends no ROI, confidence, or confirmation settings', async () => {
    const f = fixture();
    f.context.requests = [];
    f.run(
        "state.flow.runs=[{id:'selected-run',manifest:{training_completed_beijing:'2026-10-27T15:59:05+08:00'}}];runId='selected-run';api=async(path,body)=>{requests.push({path,body});if(path.endsWith('/prepare_deploy'))return{nonce:'explicit-choice',selection:{project_name:'1027',training_started_beijing:'start',training_completed_beijing:'end',batch:'batch',fine_sha256:'selected-hash',config_sha256:'config-hash'}};return{id:'deploy-job',kind:'deploy',state:'queued'};};modal=(title,html,label,handler)=>{globalThis.confirmModel=handler;};",
    );
    await f.element('prepareDeploy').handlers.click({ currentTarget: f.element('prepareDeploy') });
    await f.context.confirmModel();
    const deploy = f.context.requests.find((request) => request.path.endsWith('/deploy'));
    assert.equal(deploy.body.nonce, 'explicit-choice');
    assert.equal(deploy.body.confirmed, true);
    assert.equal(Object.hasOwn(deploy.body, 'values'), false);
    assert.equal(Object.hasOwn(deploy.body, 'rectangle'), false);
    assert.deepEqual(Array.from(f.run('rects.legal')), [704, 396, 512, 288]);
});

(async () => {
    for (const [name, fn] of cases) {
        await fn();
        console.log('PASS ' + name);
    }
    console.log(
        JSON.stringify({ passed: cases.length, browser_started: false, model_loaded: false }),
    );
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
