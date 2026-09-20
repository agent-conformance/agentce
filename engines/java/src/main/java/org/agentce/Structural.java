package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.math.BigInteger;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The structural evaluator: compile a PSP shape to queries over the graph store (SPEC §7.2, §9.2).
 *
 * <p>Target nodes are the applicable population; each is checked against the shape's property shapes,
 * and a node with any failing constraint is a failure. Outcomes are computed at the population level
 * against the control's {@code tolerance}. Class membership uses the materialised {@code rdfs:subClassOf*}
 * closure. Each violation is {@code {focus, path, constraint, message_key}}. A faithful port of the
 * reference; ratio tolerances are compared exactly with {@link BigInteger} cross-multiplication.
 */
public final class Structural {
    private Structural() {}

    /** A resolved value: an IRI or a typed literal. */
    private static final class Value {
        final String repr;
        final boolean isIri;
        final String datatype;

        Value(String repr, boolean isIri, String datatype) {
            this.repr = repr;
            this.isIri = isIri;
            this.datatype = datatype;
        }
    }

    public static final class Violation {
        public final String focus;
        public final String path;
        public final String constraint;
        public final String messageKey;

        public Violation(String focus, String path, String constraint, String messageKey) {
            this.focus = focus;
            this.path = path;
            this.constraint = constraint;
            this.messageKey = messageKey;
        }
    }

    public static ObjectNode violationToJson(Violation v) {
        ObjectNode out = Json.nodes().objectNode();
        out.put("focus", v.focus);
        out.put("path", v.path);
        out.put("constraint", v.constraint);
        out.put("message_key", v.messageKey);
        return out;
    }

    public static String renderPath(Psp.PathExpr path) {
        return switch (path.kind) {
            case PREDICATE -> path.iri;
            case INVERSE -> "^" + renderPath(path.path);
            case SEQUENCE -> "(" + joinPaths(path.parts, " ") + ")";
            case ALTERNATIVE -> "(" + joinPaths(path.parts, " | ") + ")";
        };
    }

    private static String joinPaths(List<Psp.PathExpr> parts, String sep) {
        List<String> rendered = new ArrayList<>();
        for (Psp.PathExpr p : parts) {
            rendered.add(renderPath(p));
        }
        return String.join(sep, rendered);
    }

    private static List<Value> predicateValues(GraphStore store, String focus, String predicate) {
        List<Value> values = new ArrayList<>();
        for (String obj : store.objects(focus, predicate)) {
            values.add(new Value(obj, true, null));
        }
        for (String[] pair : store.literalPairs(focus, predicate)) {
            values.add(new Value(pair[0], false, pair[1]));
        }
        return values;
    }

    private static List<Value> resolvePath(GraphStore store, String focus, Psp.PathExpr path) {
        switch (path.kind) {
            case PREDICATE:
                return predicateValues(store, focus, path.iri);
            case INVERSE:
                if (path.path.kind == Psp.PathExpr.Kind.PREDICATE) {
                    List<Value> out = new ArrayList<>();
                    for (String s : store.subjects(path.path.iri, focus)) {
                        out.add(new Value(s, true, null));
                    }
                    return out;
                }
                return List.of(); // deeper inverses are outside the profile
            case ALTERNATIVE: {
                List<Value> out = new ArrayList<>();
                for (Psp.PathExpr option : path.parts) {
                    out.addAll(resolvePath(store, focus, option));
                }
                return out;
            }
            case SEQUENCE:
            default: {
                List<String> current = List.of(focus);
                for (int index = 0; index < path.parts.size(); index++) {
                    List<Value> collected = new ArrayList<>();
                    for (String node : current) {
                        collected.addAll(resolvePath(store, node, path.parts.get(index)));
                    }
                    if (index == path.parts.size() - 1) {
                        return collected;
                    }
                    List<String> next = new ArrayList<>();
                    for (Value v : collected) {
                        if (v.isIri) {
                            next.add(v.repr);
                        }
                    }
                    current = next;
                }
                return List.of();
            }
        }
    }

    private static final Pattern INTEGER = Pattern.compile("[+-]?[0-9]+");
    private static final Pattern DATETIME = Pattern.compile(
            "([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
                    + "(?:\\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})?");
    private static final int[] DAYS_IN_MONTH = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};

    /** Days since 1970-01-01 in the proleptic Gregorian calendar (exact integer arithmetic). */
    private static long daysFromCivil(int yearIn, int month, int day) {
        long year = month <= 2 ? yearIn - 1L : yearIn;
        long era = Math.floorDiv(year, 400L);
        long yoe = year - era * 400;
        long doy = (153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1;
        long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        return era * 146097 + doe - 719468;
    }

    /** A parsed date-time: whether it carries an offset, whole seconds since the epoch in UTC, and the fraction digits. */
    private record Instant(boolean aware, long seconds, String fraction) {}

    private static Instant parseDatetime(String value) {
        Matcher match = DATETIME.matcher(value);
        if (!match.matches()) {
            return null;
        }
        int year = Integer.parseInt(match.group(1));
        int month = Integer.parseInt(match.group(2));
        int day = Integer.parseInt(match.group(3));
        int hour = Integer.parseInt(match.group(4));
        int minute = Integer.parseInt(match.group(5));
        int second = Integer.parseInt(match.group(6));
        boolean leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        if (month < 1 || month > 12) {
            return null;
        }
        if (day < 1 || day > DAYS_IN_MONTH[month - 1] + (leap && month == 2 ? 1 : 0)) {
            return null;
        }
        if (hour > 23 || minute > 59 || second > 59) {
            return null;
        }
        String zone = match.group(8);
        long offset = 0;
        if (zone != null && !zone.equals("Z")) {
            int offsetHours = Integer.parseInt(zone.substring(1, 3));
            int offsetMinutes = Integer.parseInt(zone.substring(4, 6));
            if (offsetHours > 23 || offsetMinutes > 59) {
                return null;
            }
            offset = (offsetHours * 3600L + offsetMinutes * 60L) * (zone.charAt(0) == '-' ? -1 : 1);
        }
        long seconds = daysFromCivil(year, month, day) * 86400L + hour * 3600L + minute * 60L + second - offset;
        String fraction = match.group(7);
        return new Instant(zone != null, seconds, fraction == null ? "" : fraction);
    }

    /**
     * Order two literals exactly: -1, 0, 1, or {@code null} when they are not comparable.
     *
     * <p>Only {@code xsd:integer} and {@code xsd:dateTime} lexical forms are comparable
     * ({@code spec/rules/psp.md}). Integers compare as exact integers of any size; date-times compare as
     * exact instants to arbitrary fractional-second precision, and an aware date-time is never comparable
     * with a naive one. Nothing goes through IEEE-754 or a runtime date type, so every engine returns the
     * same answer.
     */
    public static Integer compareLiterals(String a, String b) {
        if (INTEGER.matcher(a).matches() && INTEGER.matcher(b).matches()) {
            return Integer.signum(new BigInteger(a.startsWith("+") ? a.substring(1) : a)
                    .compareTo(new BigInteger(b.startsWith("+") ? b.substring(1) : b)));
        }
        Instant da = parseDatetime(a);
        Instant db = parseDatetime(b);
        if (da == null || db == null || da.aware() != db.aware()) {
            return null;
        }
        if (da.seconds() != db.seconds()) {
            return da.seconds() < db.seconds() ? -1 : 1;
        }
        int width = Math.max(da.fraction().length(), db.fraction().length());
        String fa = padEnd(da.fraction(), width);
        String fb = padEnd(db.fraction(), width);
        return Integer.signum(fa.compareTo(fb));
    }

    private static String padEnd(String digits, int width) {
        return digits + "0".repeat(width - digits.length());
    }

    private static boolean le(String a, String b) {
        Integer order = compareLiterals(a, b);
        return order != null && order <= 0;
    }

    private static boolean lt(String a, String b) {
        Integer order = compareLiterals(a, b);
        return order != null && order < 0;
    }

    private static Set<String> reprs(List<Value> values) {
        Set<String> out = new LinkedHashSet<>();
        for (Value v : values) {
            out.add(v.repr);
        }
        return out;
    }

    private static boolean setsEqual(Set<String> a, Set<String> b) {
        return a.equals(b);
    }

    private static boolean intersects(Set<String> a, Set<String> b) {
        for (String item : a) {
            if (b.contains(item)) {
                return true;
            }
        }
        return false;
    }

    private static Psp.Shape shapeByCurie(Map<String, Psp.Shape> shapes, String ref) {
        int colon = ref.indexOf(':');
        String local = colon >= 0 ? ref.substring(colon + 1) : ref;
        for (Psp.Shape shape : shapes.values()) {
            if (shape.iri.endsWith(local)) {
                return shape;
            }
        }
        return null;
    }

    private static List<Violation> nodeViolations(
            GraphStore store, String focus, Psp.Shape shape, Map<String, Psp.Shape> shapes, String controlId) {
        List<Violation> out = new ArrayList<>();
        for (Psp.PropertyShape prop : shape.properties) {
            out.addAll(checkProperty(store, focus, prop, shapes, controlId));
        }
        return out;
    }

    private static List<Violation> checkProperty(
            GraphStore store, String focus, Psp.PropertyShape prop, Map<String, Psp.Shape> shapes, String controlId) {
        List<Value> values = resolvePath(store, focus, prop.path);
        String pathText = renderPath(prop.path);
        List<String> failed = new ArrayList<>();

        if (prop.minCount != null && values.size() < prop.minCount) {
            failed.add("sh:minCount");
        }
        if (prop.maxCount != null && values.size() > prop.maxCount) {
            failed.add("sh:maxCount");
        }
        if (prop.hasValue != null && values.stream().noneMatch(v -> v.repr.equals(prop.hasValue))) {
            failed.add("sh:hasValue");
        }
        if (prop.cls != null && !values.stream().allMatch(v -> v.isIri && store.isA(v.repr, prop.cls))) {
            failed.add("sh:class");
        }
        if (prop.datatype != null && !values.stream().allMatch(v -> !v.isIri && prop.datatype.equals(v.datatype))) {
            failed.add("sh:datatype");
        }
        if (prop.nodeKind != null) {
            boolean wantIri = prop.nodeKind.equals("sh:IRI");
            if (!values.stream().allMatch(v -> v.isIri == wantIri)) {
                failed.add("sh:nodeKind");
            }
        }
        if (prop.inValues != null) {
            Set<String> allowed = new LinkedHashSet<>(prop.inValues);
            if (!values.stream().allMatch(v -> allowed.contains(v.repr))) {
                failed.add("sh:in");
            }
        }
        if (prop.minInclusive != null && !values.stream().allMatch(v -> le(prop.minInclusive, v.repr))) {
            failed.add("sh:minInclusive");
        }
        if (prop.maxInclusive != null && !values.stream().allMatch(v -> le(v.repr, prop.maxInclusive))) {
            failed.add("sh:maxInclusive");
        }
        if (prop.equals != null) {
            Set<String> others = reprs(predicateValues(store, focus, prop.equals));
            if (!setsEqual(reprs(values), others)) {
                failed.add("sh:equals");
            }
        }
        if (prop.disjoint != null) {
            Set<String> others = reprs(predicateValues(store, focus, prop.disjoint));
            if (intersects(reprs(values), others)) {
                failed.add("sh:disjoint");
            }
        }
        if (prop.lessThan != null) {
            List<Value> others = predicateValues(store, focus, prop.lessThan);
            if (!values.stream().allMatch(v -> others.stream().allMatch(o -> lt(v.repr, o.repr)))) {
                failed.add("sh:lessThan");
            }
        }
        if (prop.lessThanOrEquals != null) {
            List<Value> others = predicateValues(store, focus, prop.lessThanOrEquals);
            if (!values.stream().allMatch(v -> others.stream().allMatch(o -> le(v.repr, o.repr)))) {
                failed.add("sh:lessThanOrEquals");
            }
        }
        if (prop.node != null) {
            Psp.Shape nested = shapes.getOrDefault(prop.node, shapeByCurie(shapes, prop.node));
            if (nested != null
                    && !values.stream().allMatch(v -> v.isIri && nodeViolations(store, v.repr, nested, shapes, controlId).isEmpty())) {
                failed.add("sh:node");
            }
        }
        if (prop.qualifiedValueShape != null && prop.qualifiedMinCount != null) {
            Psp.Shape nested = shapes.getOrDefault(prop.qualifiedValueShape, shapeByCurie(shapes, prop.qualifiedValueShape));
            int conforming = 0;
            for (Value v : values) {
                if (v.isIri && nested != null && nodeViolations(store, v.repr, nested, shapes, controlId).isEmpty()) {
                    conforming += 1;
                }
            }
            if (conforming < prop.qualifiedMinCount) {
                failed.add("sh:qualifiedMinCount");
            }
        }

        String key = prop.messageKey != null
                ? prop.messageKey
                : (prop.name != null ? controlId + "." + prop.name : controlId + "." + pathText);
        List<Violation> out = new ArrayList<>();
        for (String constraint : failed) {
            out.add(new Violation(focus, pathText, constraint, key));
        }
        return out;
    }

    private static boolean hasValue(GraphStore store, String node, String predicate, String value) {
        return store.objects(node, predicate).contains(value) || store.literalValues(node, predicate).contains(value);
    }

    private static List<String> targetNodes(GraphStore store, Psp.Shape shape) {
        Set<String> focus = new LinkedHashSet<>(shape.targetNodes);
        if (shape.targetClass != null) {
            focus.addAll(store.instancesOf(shape.targetClass));
        }
        List<String> sorted = new ArrayList<>(focus);
        sorted.sort(Json::byteCompare);
        if (shape.targetWhere.isEmpty()) {
            return sorted;
        }
        List<String> out = new ArrayList<>();
        for (String node : sorted) {
            boolean all = true;
            for (String[] cond : shape.targetWhere) {
                if (!hasValue(store, node, cond[0], cond[1])) {
                    all = false;
                    break;
                }
            }
            if (all) {
                out.add(node);
            }
        }
        return out;
    }

    /** The applicable focus nodes, the failing focus nodes, and the violations for {@code shape}. */
    public static final class ShapeResult {
        public final List<String> applicable;
        public final Set<String> failing;
        public final List<Violation> violations;

        ShapeResult(List<String> applicable, Set<String> failing, List<Violation> violations) {
            this.applicable = applicable;
            this.failing = failing;
            this.violations = violations;
        }
    }

    public static ShapeResult evaluateShape(
            GraphStore store, Psp.Shape shape, Map<String, Psp.Shape> shapes, String controlId) {
        List<String> applicable = targetNodes(store, shape);
        Set<String> failing = new LinkedHashSet<>();
        List<Violation> violations = new ArrayList<>();
        for (String focus : applicable) {
            List<Violation> nodeVs = new ArrayList<>();
            for (Psp.PropertyShape prop : shape.properties) {
                nodeVs.addAll(checkProperty(store, focus, prop, shapes, controlId));
            }
            if (!nodeVs.isEmpty()) {
                failing.add(focus);
                violations.addAll(nodeVs);
            }
        }
        return new ShapeResult(applicable, failing, violations);
    }

    /** One control's structural outcome, for catalog linting and the golden cross-check. */
    public static final class ControlOutcome {
        public final String control;
        public final String outcome;
        public final int applicable;
        public final int failed;
        public final List<Violation> violations;

        ControlOutcome(String control, String outcome, int applicable, int failed, List<Violation> violations) {
            this.control = control;
            this.outcome = outcome;
            this.applicable = applicable;
            this.failed = failed;
            this.violations = violations;
        }
    }

    public static ObjectNode controlOutcomeToJson(ControlOutcome outcome) {
        ObjectNode out = Json.nodes().objectNode();
        out.put("control", outcome.control);
        out.put("outcome", outcome.outcome);
        ObjectNode pop = out.putObject("population");
        pop.put("applicable", outcome.applicable);
        pop.put("failed", outcome.failed);
        var violations = out.putArray("violations");
        for (Violation v : outcome.violations) {
            violations.add(violationToJson(v));
        }
        return out;
    }

    /** Evaluate one control's shape and apply its tolerance to reach a structural outcome. */
    public static ControlOutcome evaluateControl(
            GraphStore store, Psp.Shape shape, Map<String, Psp.Shape> shapes, String controlId, JsonNode tolerance) {
        ShapeResult result = evaluateShape(store, shape, shapes, controlId);
        boolean conformant = withinTolerance(result.applicable.size(), result.failing.size(), tolerance);
        List<Violation> sorted = new ArrayList<>(result.violations);
        sorted.sort((a, b) -> {
            int c = Json.byteCompare(a.focus, b.focus);
            return c != 0 ? c : Json.byteCompare(a.constraint, b.constraint);
        });
        return new ControlOutcome(
                controlId, conformant ? "conformant" : "non-conformant", result.applicable.size(), result.failing.size(), sorted);
    }

    private static BigInteger[] parseRatio(String max) {
        if (max.contains("/")) {
            String[] parts = max.split("/");
            return new BigInteger[] {new BigInteger(parts[0]), new BigInteger(parts[1])};
        }
        int dot = max.indexOf('.');
        if (dot >= 0) {
            String digits = max.substring(0, dot) + max.substring(dot + 1);
            int fractional = max.length() - dot - 1;
            return new BigInteger[] {new BigInteger(digits), BigInteger.TEN.pow(fractional)};
        }
        return new BigInteger[] {new BigInteger(max), BigInteger.ONE};
    }

    /** Whether {@code failed} failures out of {@code applicable} are within the control's tolerance (§9.2). */
    public static boolean withinTolerance(int applicable, int failed, JsonNode tolerance) {
        String kind = tolerance != null && tolerance.has("kind") && tolerance.get("kind").isTextual()
                ? tolerance.get("kind").textValue()
                : "count";
        if (kind.equals("ratio") && applicable > 0) {
            BigInteger[] nd = parseRatio(maxOf(tolerance));
            // failed/applicable <= num/den  <=>  failed*den <= num*applicable
            return BigInteger.valueOf(failed).multiply(nd[1]).compareTo(nd[0].multiply(BigInteger.valueOf(applicable))) <= 0;
        }
        return failed <= Integer.parseInt(maxOf(tolerance));
    }

    private static String maxOf(JsonNode tolerance) {
        if (tolerance != null && tolerance.has("max") && !tolerance.get("max").isNull()) {
            return tolerance.get("max").asText();
        }
        return "0";
    }
}
