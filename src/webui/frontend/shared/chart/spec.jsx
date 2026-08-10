// Declarative spec for `:::chart` blocks: a minimal indentation-aware
// parser, whitelist validation and a sandboxed arithmetic evaluator for
// `binds` expressions. Pure JS with no DOM and no eval, so it runs unchanged
// in Node tests. Payloads are validated here and rendered by chart/mount.
(function (root) {
  "use strict";

  var CHART_TYPES = { line: true, scatter: true, bar: true };
  var MAX_PAYLOAD_BYTES = 32 * 1024;
  var PARAM_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

  function fail(message) {
    var err = new Error(message);
    err.isSpecError = true;
    return err;
  }

  // Parse a scalar: `[...]` number arrays, quoted strings, numbers, booleans
  // or bare strings (titles).
  function parseValue(raw) {
    var text = String(raw == null ? "" : raw).trim();
    if (text === "") return "";
    if (text.charAt(0) === "[") {
      var closes = text.charAt(text.length - 1) === "]";
      var inner = closes ? text.slice(1, -1) : text.slice(1);
      if (!inner.trim()) return [];
      return inner.split(",").map(function (part) {
        var value = parseValue(part.trim());
        if (typeof value !== "number") throw fail("array elements must be numbers: " + part.trim());
        return value;
      });
    }
    if (text.length >= 2 && text.charAt(0) === '"' && text.charAt(text.length - 1) === '"') {
      return text.slice(1, -1).replace(/\\n/g, "\n").replace(/\\"/g, '"');
    }
    if (text === "true") return true;
    if (text === "false") return false;
    if (/^-?\d+(\.\d+)?$/.test(text)) return Number(text);
    return text;
  }

  // Parse the spec body (the text between `:::chart <type>` and `:::`):
  //   x: [-4,-3,-2,-1,0,1,2,3,4]
  //   y-binds: "a*x*x + b*x + c"
  //   controls:
  //     - param: a
  //       range: [-5, 5]
  //       step: 0.1
  //       default: 1
  //   options:
  //     title: y = a·x² + b·x + c
  //     grid: true
  // Only this fixed nesting depth is supported: top-level scalars, one object
  // container (`options`) and one list container (`controls`) whose items may
  // carry scalar sub-keys.
  function parseSpec(body) {
    var lines = String(body || "").replace(/\r\n?/g, "\n").split("\n");
    var spec = {};
    var controls = null;
    var item = null;
    var options = null;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (!line.trim() || /^\s*#/.test(line)) continue;
      var indent = line.length - line.replace(/^ +/, "").length;
      var content = line.trim();
      var colon = content.indexOf(":");

      if (content.indexOf("- ") === 0) {
        // A controls list item; its sub-keys use a deeper indent.
        if (!controls) controls = spec.controls = [];
        options = null;
        item = {};
        controls.push(item);
        var rest = content.slice(2);
        var itemColon = rest.indexOf(":");
        if (itemColon < 0) throw fail("invalid control item: " + content);
        item[rest.slice(0, itemColon).trim()] = parseValue(rest.slice(itemColon + 1));
        continue;
      }
      if (options) {
        if (indent === 0) options = null;
        else if (colon > 0) {
          options[content.slice(0, colon).trim()] = parseValue(content.slice(colon + 1));
          continue;
        }
      }
      if (item && indent >= 4) {
        if (colon > 0) {
          item[content.slice(0, colon).trim()] = parseValue(content.slice(colon + 1));
          continue;
        }
      }
      if (colon < 0) throw fail("invalid spec line: " + content);
      var key = content.slice(0, colon).trim();
      var value = content.slice(colon + 1).trim();
      if (!key) throw fail("empty key in spec");
      if (key === "options") {
        options = spec.options = {};
        item = null;
        continue;
      }
      if (key === "controls") {
        controls = spec.controls = [];
        options = null;
        item = null;
        continue;
      }
      spec[key] = parseValue(value);
      item = null;
      options = null;
    }
    return spec;
  }

  // ---- binds expression: recursive-descent arithmetic evaluator ----
  // Grammar: E -> T (('+'|'-') T)* ; T -> F (('*'|'/') F)* ;
  //          F -> number | ident | '(' E ')' | '-' F
  // Only digits, identifiers, `+ - * / ( )` and whitespace are accepted;
  // anything else rejects the expression.

  function tokenize(expr) {
    var tokens = [];
    var index = 0;
    var source = String(expr || "");
    while (index < source.length) {
      var ch = source.charAt(index);
      if (/\s/.test(ch)) { index += 1; continue; }
      if (/[+\-*/(),]/.test(ch)) {
        tokens.push({ kind: "op", value: ch });
        index += 1;
        continue;
      }
      if (/[0-9.]/.test(ch)) {
        var num = source.slice(index).match(/^\d+(\.\d+)?|^\.\d+/);
        if (!num) throw fail("invalid number in expression");
        tokens.push({ kind: "num", value: Number(num[0]) });
        index += num[0].length;
        continue;
      }
      if (/[A-Za-z_]/.test(ch)) {
        var ident = source.slice(index).match(/^[A-Za-z_][A-Za-z0-9_]*/);
        if (!ident) throw fail("invalid identifier in expression");
        tokens.push({ kind: "ident", value: ident[0] });
        index += ident[0].length;
        continue;
      }
      throw fail("unsupported character in expression: " + ch);
    }
    return tokens;
  }

  function compileExpr(expr) {
    var tokens = tokenize(expr);
    var pos = 0;

    function peek() { return tokens[pos]; }
    function next() { return tokens[pos++]; }
    function expectOp(op) {
      var token = next();
      if (!token || token.kind !== "op" || token.value !== op) throw fail("unexpected token in expression");
    }

    function parseExpression() {
      var left = parseTerm();
      while (peek() && peek().kind === "op" && (peek().value === "+" || peek().value === "-")) {
        var op = next().value;
        var right = parseTerm();
        left = { op: op, left: left, right: right };
      }
      return left;
    }

    function parseTerm() {
      var left = parseFactor();
      while (peek() && peek().kind === "op" && (peek().value === "*" || peek().value === "/")) {
        var op = next().value;
        var right = parseFactor();
        left = { op: op, left: left, right: right };
      }
      return left;
    }

    function parseFactor() {
      var token = next();
      if (!token) throw fail("incomplete expression");
      if (token.kind === "num") return { num: token.value };
      if (token.kind === "ident") return { ident: token.value };
      if (token.kind === "op" && token.value === "-") return { neg: parseFactor() };
      if (token.kind === "op" && token.value === "(") {
        var inner = parseExpression();
        expectOp(")");
        return inner;
      }
      throw fail("unexpected token in expression");
    }

    var ast = parseExpression();
    if (pos < tokens.length) throw fail("trailing tokens in expression");

    function evalNode(node, env) {
      if (node.num !== undefined) return node.num;
      if (node.ident !== undefined) {
        if (!Object.prototype.hasOwnProperty.call(env, node.ident)) throw fail("unknown variable in expression: " + node.ident);
        return Number(env[node.ident]);
      }
      if (node.neg) return -evalNode(node.neg, env);
      var leftValue = evalNode(node.left, env);
      var rightValue = evalNode(node.right, env);
      if (node.op === "+") return leftValue + rightValue;
      if (node.op === "-") return leftValue - rightValue;
      if (node.op === "*") return leftValue * rightValue;
      if (node.op === "/") return leftValue / rightValue;
      throw fail("invalid expression node");
    }

    return {
      evaluate: function (env) { return evalNode(ast, env || {}); },
      variables: function () {
        var names = [];
        (function collect(node) {
          if (!node) return;
          if (node.ident) names.push(node.ident);
          collect(node.left); collect(node.right); collect(node.neg);
        })(ast);
        return names.filter(function (name, idx) { return names.indexOf(name) === idx; });
      },
    };
  }

  // ---- validation ----

  function isNumberArray(value) {
    return Array.isArray(value) && value.length > 0 && value.every(function (n) { return typeof n === "number"; });
  }

  function validateSpec(spec) {
    if (!spec || typeof spec !== "object") throw fail("empty chart spec");
    if (!CHART_TYPES[String(spec.type || "")]) throw fail("unsupported chart type: " + String(spec.type || ""));
    if (!isNumberArray(spec.x)) throw fail("chart spec requires a non-empty numeric `x` array");
    var controls = Array.isArray(spec.controls) ? spec.controls : [];
    var params = [];
    for (var i = 0; i < controls.length; i++) {
      var control = controls[i];
      var param = String(control && control.param || "");
      if (!PARAM_NAME_RE.test(param)) throw fail("invalid control param name: " + param);
      if (param === "x") throw fail("control param `x` is reserved");
      if (params.indexOf(param) >= 0) throw fail("duplicate control param: " + param);
      params.push(param);
      var range = control.range;
      if (!isNumberArray(range) || range.length !== 2 || range[0] >= range[1]) {
        throw fail("control `" + param + "` requires range: [min, max] with min < max");
      }
      if (!(typeof control.step === "number" && control.step > 0)) {
        throw fail("control `" + param + "` requires a positive step");
      }
      if (typeof control.default !== "number") throw fail("control `" + param + "` requires a numeric default");
    }
    if (spec["y-binds"] !== undefined) {
      if (typeof spec["y-binds"] !== "string" || !String(spec["y-binds"]).trim()) {
        throw fail("y-binds must be a non-empty expression string");
      }
    } else if (!isNumberArray(spec.y) || spec.y.length !== spec.x.length) {
      throw fail("chart spec requires `y` (same length as x) or `y-binds`");
    }
    var options = spec.options || {};
    if (options.title !== undefined && typeof options.title !== "string") throw fail("options.title must be a string");
    if (options.grid !== undefined && typeof options.grid !== "boolean") throw fail("options.grid must be a boolean");
    if (options.color !== undefined && typeof options.color !== "string") throw fail("options.color must be a string");
    ["x-min", "x-max", "y-min", "y-max"].forEach(function (key) {
      if (options[key] !== undefined && typeof options[key] !== "number") throw fail("options." + key + " must be a number");
    });
    return spec;
  }

  function buildPayload(body, chartType) {
    var spec = parseSpec(body);
    // The chart type lives on the opening line (`:::chart line`); a `type:`
    // key inside the body is honoured as an override.
    spec.type = String(spec.type || chartType || "").trim();
    validateSpec(spec);
    var compiled = null;
    if (spec["y-binds"]) {
      compiled = compileExpr(spec["y-binds"]);
      var allowed = compiled.variables().filter(function (name) { return name !== "x"; });
      for (var i = 0; i < allowed.length; i++) {
        var found = (spec.controls || []).some(function (c) { return c.param === allowed[i]; });
        if (!found) throw fail("expression variable has no control: " + allowed[i]);
      }
    }
    var payloadSpec = { type: spec.type, x: spec.x, controls: spec.controls || [], options: spec.options || {} };
    if (spec.y !== undefined) payloadSpec.y = spec.y;
    if (spec["y-binds"] !== undefined) payloadSpec["y-binds"] = spec["y-binds"];
    var json = JSON.stringify(payloadSpec);
    if (json.length > MAX_PAYLOAD_BYTES) throw fail("chart spec exceeds the 32 KB payload limit");
    return { spec: spec, compiled: compiled, json: json };
  }

  // ---- :::button blocks ----

  var ACTION_ID_RE = /^[a-z0-9_]+$/;
  var MAX_ACTION_ID_LENGTH = 32;
  var MAX_VALUE_LENGTH = 256;
  var BUTTON_STYLES = { primary: true, default: true, danger: true };
  var BUTTON_MODES = { local: true, model: true };

  function buildButtonPayload(body) {
    var spec = parseSpec(body);
    var actionId = String(spec.action_id || "");
    if (!ACTION_ID_RE.test(actionId)) throw fail("action_id must match [a-z0-9_]+");
    if (actionId.length > MAX_ACTION_ID_LENGTH) throw fail("action_id exceeds 32 characters");
    var label = String(spec.label || "");
    if (!label.trim()) throw fail("button requires a non-empty label");
    if (spec.style !== undefined && !BUTTON_STYLES[String(spec.style)]) {
      throw fail("unsupported button style: " + String(spec.style));
    }
    if (spec.mode !== undefined && !BUTTON_MODES[String(spec.mode)]) {
      throw fail("unsupported button mode: " + String(spec.mode));
    }
    var value = spec.value === undefined ? "" : String(spec.value);
    if (value.length > MAX_VALUE_LENGTH) throw fail("button value exceeds 256 characters");
    if (spec.disabled !== undefined && typeof spec.disabled !== "boolean") {
      throw fail("button disabled must be a boolean");
    }
    var payloadSpec = {
      action_id: actionId,
      label: label,
      style: String(spec.style || "default"),
      mode: String(spec.mode || "local"),
      value: value,
      disabled: spec.disabled === true,
    };
    var json = JSON.stringify(payloadSpec);
    if (json.length > MAX_PAYLOAD_BYTES) throw fail("button spec exceeds the 32 KB payload limit");
    return { spec: payloadSpec, json: json };
  }

  var service = {
    CHART_TYPES: CHART_TYPES,
    MAX_PAYLOAD_BYTES: MAX_PAYLOAD_BYTES,
    parseValue: parseValue,
    parseSpec: parseSpec,
    compileExpr: compileExpr,
    validateSpec: validateSpec,
    buildPayload: buildPayload,
    buildButtonPayload: buildButtonPayload,
  };
  root.CyreneUI.chartSpec = root.CyreneUI.register("chart-spec", service);
})(window);
