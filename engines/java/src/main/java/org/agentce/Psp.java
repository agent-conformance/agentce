package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Parse a Portable Shape Profile shape into a small AST (SPEC §7.2, ADR-0002). A node shape has targets
 * ({@code sh:targetClass}, {@code sh:targetNode}, and the engine-resolved {@code agentce:targetWhere}
 * property-value conjunction) and property shapes, each with a path (a predicate, an inverse, or a
 * bounded sequence or alternative) and the profile's constraints. A faithful port of the reference; it
 * compacts terms to the store's CURIE representation identically.
 *
 * <p>{@code spec/rules/psp_check.py} is the authoring-time checker for the profile; this class reads the
 * same term list ({@code psp-terms.json}) and refuses every shape that checker refuses, and every shape
 * file that will not parse, under a stable message key before a shape ever reaches the evaluator (18.34,
 * 18.78).
 */
public final class Psp {
    private Psp() {}

    private static final String SH = "http://www.w3.org/ns/shacl#";
    private static final String AGENTCE = "https://agent-conformance.org/vocab/evidence/v1#";
    private static final String PROV = "http://www.w3.org/ns/prov#";
    private static final String RDFS = "http://www.w3.org/2000/01/rdf-schema#";
    private static final String XSD = "http://www.w3.org/2001/XMLSchema#";

    /** Prefix compaction order, matching the reference. */
    private static final String[][] PREFIXES = {
        {"agentce:", AGENTCE},
        {"prov:", PROV},
        {"sh:", SH},
        {"rdfs:", RDFS},
        {"xsd:", XSD},
    };

    public static final class PathExpr {
        public enum Kind {
            PREDICATE,
            INVERSE,
            SEQUENCE,
            ALTERNATIVE
        }

        public final Kind kind;
        public final String iri; // predicate
        public final PathExpr path; // inverse
        public final List<PathExpr> parts; // sequence steps / alternative options

        private PathExpr(Kind kind, String iri, PathExpr path, List<PathExpr> parts) {
            this.kind = kind;
            this.iri = iri;
            this.path = path;
            this.parts = parts;
        }

        static PathExpr predicate(String iri) {
            return new PathExpr(Kind.PREDICATE, iri, null, null);
        }

        static PathExpr inverse(PathExpr path) {
            return new PathExpr(Kind.INVERSE, null, path, null);
        }

        static PathExpr sequence(List<PathExpr> steps) {
            return new PathExpr(Kind.SEQUENCE, null, null, steps);
        }

        static PathExpr alternative(List<PathExpr> options) {
            return new PathExpr(Kind.ALTERNATIVE, null, null, options);
        }
    }

    public static final class PropertyShape {
        public PathExpr path;
        public String name;
        public String messageKey;
        public Integer minCount;
        public Integer maxCount;
        public String cls;
        public String datatype;
        public String nodeKind;
        public List<String> inValues;
        public String hasValue;
        public String node;
        public String qualifiedValueShape;
        public Integer qualifiedMinCount;
        public String equals;
        public String disjoint;
        public String lessThan;
        public String lessThanOrEquals;
        public String minInclusive;
        public String maxInclusive;
    }

    public static final class Shape {
        public String iri;
        /** Every {@code sh:targetClass}, sorted: SHACL reads more than one as a union. */
        public List<String> targetClasses = new ArrayList<>();
        public List<String> targetNodes = new ArrayList<>();
        public List<String[]> targetWhere = new ArrayList<>();
        public List<PropertyShape> properties = new ArrayList<>();
    }

    /** Compact an RDF term to the store's representation (CURIE for known IRIs; lexical literal). */
    public static String curie(Rdf.Term term) {
        if (term.isLiteral()) {
            return term.value;
        }
        String text = term.value;
        if (text.equals(Rdf.RDF_TYPE)) {
            return "rdf:type";
        }
        for (String[] pair : PREFIXES) {
            if (text.startsWith(pair[1])) {
                return pair[0] + text.substring(pair[1].length());
            }
        }
        return text;
    }

    private static Rdf.Term sh(String name) {
        return Rdf.Term.iri(SH + name);
    }

    private static List<Rdf.Term> collection(Rdf.Store store, Rdf.Term head) {
        List<Rdf.Term> items = new ArrayList<>();
        Rdf.Term current = head;
        while (current != null && !current.value.equals(Rdf.RDF_NIL)) {
            Rdf.Term first = store.value(current, Rdf.Term.iri(Rdf.RDF_FIRST));
            if (first == null) {
                break;
            }
            items.add(first);
            current = store.value(current, Rdf.Term.iri(Rdf.RDF_REST));
        }
        return items;
    }

    private static Integer intValue(Rdf.Term term) {
        return term != null && term.isLiteral() ? Integer.parseInt(term.value) : null;
    }

    private static PathExpr parsePath(Rdf.Store store, Rdf.Term node) {
        if (node.isIri()) {
            return PathExpr.predicate(curie(node));
        }
        Rdf.Term inverse = store.value(node, sh("inversePath"));
        if (inverse != null) {
            return PathExpr.inverse(parsePath(store, inverse));
        }
        Rdf.Term alternative = store.value(node, sh("alternativePath"));
        if (alternative != null) {
            List<PathExpr> options = new ArrayList<>();
            for (Rdf.Term item : collection(store, alternative)) {
                options.add(parsePath(store, item));
            }
            return PathExpr.alternative(options);
        }
        if (node.isBlank()) {
            List<PathExpr> steps = new ArrayList<>();
            for (Rdf.Term item : collection(store, node)) {
                steps.add(parsePath(store, item));
            }
            return PathExpr.sequence(steps);
        }
        return PathExpr.predicate(curie(node));
    }

    private static PropertyShape parseProperty(Rdf.Store store, Rdf.Term node) {
        PropertyShape prop = new PropertyShape();
        prop.path = parsePath(store, store.value(node, sh("path")));
        Rdf.Term name = store.value(node, sh("name"));
        prop.name = name != null ? name.value : null;
        Rdf.Term messageKey = store.value(node, Rdf.Term.iri(AGENTCE + "messageKey"));
        prop.messageKey = messageKey != null ? messageKey.value : null;
        prop.minCount = intValue(store.value(node, sh("minCount")));
        prop.maxCount = intValue(store.value(node, sh("maxCount")));

        prop.cls = curieAttr(store, node, "class");
        prop.datatype = curieAttr(store, node, "datatype");
        prop.nodeKind = curieAttr(store, node, "nodeKind");
        prop.node = curieAttr(store, node, "node");
        prop.equals = curieAttr(store, node, "equals");
        prop.disjoint = curieAttr(store, node, "disjoint");
        prop.lessThan = curieAttr(store, node, "lessThan");
        prop.lessThanOrEquals = curieAttr(store, node, "lessThanOrEquals");

        Rdf.Term minInc = store.value(node, sh("minInclusive"));
        prop.minInclusive = minInc != null ? minInc.value : null;
        Rdf.Term maxInc = store.value(node, sh("maxInclusive"));
        prop.maxInclusive = maxInc != null ? maxInc.value : null;

        Rdf.Term hasValue = store.value(node, sh("hasValue"));
        if (hasValue != null) {
            prop.hasValue = curie(hasValue);
        }
        Rdf.Term inList = store.value(node, sh("in"));
        if (inList != null) {
            List<String> values = new ArrayList<>();
            for (Rdf.Term item : collection(store, inList)) {
                values.add(curie(item));
            }
            prop.inValues = values;
        }
        Rdf.Term qualified = store.value(node, sh("qualifiedValueShape"));
        if (qualified != null) {
            prop.qualifiedValueShape = qualified.value;
            prop.qualifiedMinCount = intValue(store.value(node, sh("qualifiedMinCount")));
        }
        return prop;
    }

    private static String curieAttr(Rdf.Store store, Rdf.Term node, String name) {
        Rdf.Term found = store.value(node, sh(name));
        return found != null ? curie(found) : null;
    }

    /** Parse every {@code sh:NodeShape} in the store into the PSP AST, keyed by shape IRI. */
    public static Map<String, Shape> parseShapes(Rdf.Store store) {
        Map<String, Shape> shapes = new LinkedHashMap<>();
        for (Rdf.Term shapeNode : store.subjects(Rdf.Term.iri(Rdf.RDF_TYPE), sh("NodeShape"))) {
            Shape shape = new Shape();
            shape.iri = shapeNode.value;
            for (Rdf.Term targetClass : store.objects(shapeNode, sh("targetClass"))) {
                shape.targetClasses.add(curie(targetClass));
            }
            shape.targetClasses.sort(Json::byteCompare);
            for (Rdf.Term node : store.objects(shapeNode, sh("targetNode"))) {
                shape.targetNodes.add(curie(node));
            }
            for (Rdf.Term where : store.objects(shapeNode, Rdf.Term.iri(AGENTCE + "targetWhere"))) {
                for (Rdf.Term[] quad : store.quadsOf(where)) {
                    if (!quad[1].value.equals(Rdf.RDF_TYPE)) {
                        shape.targetWhere.add(new String[] {curie(quad[1]), curie(quad[2])});
                    }
                }
            }
            for (Rdf.Term propNode : store.objects(shapeNode, sh("property"))) {
                shape.properties.add(parseProperty(store, propNode));
            }
            shapes.put(shapeNode.value, shape);
        }
        return shapes;
    }

    /** The Portable Shape Profile's term lists (spec/rules/psp-terms.json, vendored byte-identical and
     * held in sync by BundledDataTest): the same file spec/rules/psp_check.py reads, so the engine refuses
     * exactly the shapes the authoring-time checker refuses (18.78). Left unchecked, an excluded predicate
     * parses as ordinary Turtle and the reads above, which only ask for the predicates they name, drop it
     * silently: the shape would be evaluated as if the construct were not there. */
    private static final JsonNode TERMS = loadTerms();
    private static final List<String> PRIORITY_DENY = textList(TERMS.get("priority_deny"));
    private static final Set<String> ALLOWED = textSet(TERMS.get("allowed"));
    private static final Set<String> RANGE_DATATYPES = textSet(TERMS.get("range_datatypes"));
    private static final String REGEX_META = TERMS.get("regex_meta").asText();
    private static final int MAX_PATH_LENGTH = TERMS.get("max_path_length").asInt();

    /** The two features 18.34 gave their own keys; every other excluded feature is outside_profile. */
    private static final Map<String, String> FEATURE_KEYS = Map.of(
            "sh:sparql", "catalog.shape.sparql_forbidden",
            "sh:js", "catalog.shape.script_forbidden",
            "sh:javascript", "catalog.shape.script_forbidden");
    private static final String OUTSIDE_PROFILE = "catalog.shape.outside_profile";
    private static final String PARSE_ERROR = "catalog.shape.parse_error";

    private static JsonNode loadTerms() {
        try (InputStream in = Psp.class.getResourceAsStream("/psp-terms.json")) {
            if (in == null) {
                throw new IllegalStateException("vendored psp-terms.json not on classpath");
            }
            return Json.parse(new String(in.readAllBytes(), StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException("cannot read psp-terms.json: " + e.getMessage(), e);
        }
    }

    private static Set<String> textSet(JsonNode array) {
        return new HashSet<>(textList(array));
    }

    /** The array's strings in file order (priority_deny's order decides which feature is reported). */
    private static List<String> textList(JsonNode array) {
        List<String> out = new ArrayList<>();
        array.forEach(item -> out.add(item.asText()));
        return List.copyOf(out);
    }

    /** The excluded feature a property path uses, or null: a predicate, an inverse of a permitted path,
     * or a sequence or alternative of at most {@code max_path_length} permitted paths. */
    private static String pathFeature(Rdf.Store store, Rdf.Term node, Set<Rdf.Term> seen) {
        if (node.isIri()) {
            return null;
        }
        if (seen.contains(node)) {
            return "path (cyclic)";
        }
        Set<Rdf.Term> inner = new HashSet<>(seen);
        inner.add(node);
        for (String banned : List.of("zeroOrMorePath", "oneOrMorePath", "zeroOrOnePath")) {
            if (store.value(node, sh(banned)) != null) {
                return "sh:" + banned;
            }
        }
        Rdf.Term inverse = store.value(node, sh("inversePath"));
        if (inverse != null) {
            return pathFeature(store, inverse, inner);
        }
        Rdf.Term alternative = store.value(node, sh("alternativePath"));
        if (alternative != null) {
            if (store.value(alternative, Rdf.Term.iri(Rdf.RDF_FIRST)) == null) {
                return "sh:alternativePath (not a list)";
            }
            return membersFeature(store, collection(store, alternative), "alternative", inner);
        }
        if (store.value(node, Rdf.Term.iri(Rdf.RDF_FIRST)) != null) {
            return membersFeature(store, collection(store, node), "sequence", inner);
        }
        return "path (unsupported blank node)";
    }

    private static String membersFeature(Rdf.Store store, List<Rdf.Term> members, String kind, Set<Rdf.Term> seen) {
        if (members.size() > MAX_PATH_LENGTH) {
            return "path " + kind + " of " + members.size() + " (max " + MAX_PATH_LENGTH + ")";
        }
        for (Rdf.Term member : members) {
            String feature = pathFeature(store, member, seen);
            if (feature != null) {
                return feature;
            }
        }
        return null;
    }

    private static boolean nonLiteralPattern(String text) {
        if (!text.startsWith("^")) {
            return true;
        }
        return text.substring(1).codePoints().anyMatch(c -> REGEX_META.indexOf(c) >= 0);
    }

    /** The path, pattern, range and targetWhere stages, in that order, each as the features it finds. */
    private static List<List<String>> structuralFeatures(Rdf.Store store) {
        List<String> paths = new ArrayList<>();
        for (Rdf.Term node : store.objectsOf(sh("path"))) {
            String feature = pathFeature(store, node, Set.of());
            if (feature != null) {
                paths.add(feature);
            }
        }
        List<String> patterns = new ArrayList<>();
        for (Rdf.Term value : store.objectsOf(sh("pattern"))) {
            if (nonLiteralPattern(value.value)) {
                patterns.add("sh:pattern (non-literal regex)");
            }
        }
        List<String> ranges = new ArrayList<>();
        for (String name : List.of("minInclusive", "maxInclusive")) {
            for (Rdf.Term value : store.objectsOf(sh(name))) {
                if (!value.isLiteral() || !RANGE_DATATYPES.contains(value.datatype)) {
                    ranges.add(name + " on a non-integer/dateTime bound");
                }
            }
        }
        List<String> targetWhere = new ArrayList<>();
        for (Rdf.Term where : store.objectsOf(Rdf.Term.iri(AGENTCE + "targetWhere"))) {
            for (Rdf.Term[] quad : store.quadsOf(where)) {
                if (quad[2].isBlank()) {
                    targetWhere.add("agentce:targetWhere (nested node, not a value equality)");
                }
            }
        }
        return List.of(paths, patterns, ranges, targetWhere);
    }

    /** The first feature the store uses that the Portable Shape Profile excludes, or null. The order is
     * fixed so the answer never depends on triple order, and matches Python's {@code profile_feature}:
     * the priority terms in list order, matched case-insensitively and named canonically ({@code
     * sh:CLOSED} is {@code sh:closed}); then any other SHACL-namespace predicate the profile does not
     * allow, as written, least by code point; then the path, pattern, range and targetWhere stages, each
     * naming the code-point-least feature it finds (spec/rules/psp.md). */
    public static String profileFeature(Rdf.Store store) {
        List<String> used = new ArrayList<>();
        for (String predicate : store.predicates()) {
            if (predicate.startsWith(SH)) {
                used.add(predicate.substring(SH.length()));
            }
        }
        used.sort(Json::byteCompare);
        Set<String> folded = new HashSet<>();
        for (String name : used) {
            folded.add(name.toLowerCase(Locale.ROOT));
        }
        for (String term : PRIORITY_DENY) {
            if (folded.contains(term.toLowerCase(Locale.ROOT))) {
                return "sh:" + term;
            }
        }
        for (String name : used) {
            if (!ALLOWED.contains(name)) {
                return "sh:" + name;
            }
        }
        for (List<String> found : structuralFeatures(store)) {
            if (!found.isEmpty()) {
                return found.stream().min(Json::byteCompare).orElseThrow();
            }
        }
        return null;
    }

    /** Parse PSP shapes from Turtle, refusing a file that will not parse or a shape outside the profile.
     * {@code shown} names the shape file in the message (a catalog-relative path). */
    public static Map<String, Shape> parseShapesTtl(String text, String shown) {
        Rdf.Store store;
        try {
            store = Rdf.parse(text);
        } catch (RuntimeException e) { // the hand-written lexer and parser signal bad Turtle this way
            String detail = String.join(" ", String.valueOf(e.getMessage()).trim().split("\\s+"));
            throw new InputError(
                    PARSE_ERROR,
                    ErrorCatalogue.errorCause(PARSE_ERROR)
                            .replace("{path}", shown)
                            .replace("{detail}", detail.substring(0, Math.min(200, detail.length()))),
                    ErrorCatalogue.errorFix(PARSE_ERROR));
        }
        String feature = profileFeature(store);
        if (feature != null) {
            String key = FEATURE_KEYS.getOrDefault(feature, OUTSIDE_PROFILE);
            throw new InputError(
                    key,
                    ErrorCatalogue.errorCause(key).replace("{path}", shown).replace("{feature}", feature),
                    ErrorCatalogue.errorFix(key));
        }
        return parseShapes(store);
    }

    public static Map<String, Shape> parseShapesTtl(String text) {
        return parseShapesTtl(text, "(inline)");
    }

    public static Map<String, Shape> loadShapes(java.nio.file.Path path, String shown) {
        try {
            return parseShapesTtl(Files.readString(path, StandardCharsets.UTF_8), shown);
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot read shapes " + path + ": " + e.getMessage(), e);
        }
    }
}
