package org.agentce;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Parse a Portable Shape Profile shape into a small AST (SPEC §7.2, ADR-0002). A node shape has targets
 * ({@code sh:targetClass}, {@code sh:targetNode}, and the engine-resolved {@code agentce:targetWhere}
 * property-value conjunction) and property shapes, each with a path (a predicate, an inverse, or a
 * bounded sequence or alternative) and the profile's constraints. A faithful port of the reference; it
 * compacts terms to the store's CURIE representation identically.
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
        public String targetClass;
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
            Rdf.Term targetClass = store.value(shapeNode, sh("targetClass"));
            if (targetClass != null) {
                shape.targetClass = curie(targetClass);
            }
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

    public static Map<String, Shape> parseShapesTtl(String text) {
        return parseShapes(Rdf.parse(text));
    }

    public static Map<String, Shape> loadShapes(java.nio.file.Path path) {
        try {
            return parseShapesTtl(Files.readString(path, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot read shapes " + path + ": " + e.getMessage(), e);
        }
    }
}
