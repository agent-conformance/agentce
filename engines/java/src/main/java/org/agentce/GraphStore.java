package org.agentce;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * The evidence graph store (ADR-0001), an in-memory triple set.
 *
 * <p>The Python reference holds the graph in SQLite; this engine holds the same triples in memory and
 * answers the same queries in the same order (every read is sorted by UTF-8 bytes, matching SQLite's
 * BINARY collation) so the engines build byte-identical evidence. Triples are a set: writes are
 * idempotent. IRIs are compact CURIEs.
 */
public final class GraphStore {
    public static final String RDF_TYPE = "rdf:type";
    private static final char US = '';

    /** key {@code s\x1fp} -> objects. */
    private final Map<String, Set<String>> spo = new TreeMap<>();
    /** key {@code p\x1fo} -> subjects. */
    private final Map<String, Set<String>> pos = new TreeMap<>();
    /** key {@code s\x1fp} -> {@code val\x1fdatatype} literals. */
    private final Map<String, Set<String>> lit = new TreeMap<>();
    /** {@code descendant\x1fancestor} pairs. */
    private final Set<String> closure = new LinkedHashSet<>();
    /** ancestor -> descendants. */
    private final Map<String, Set<String>> descendantsByAncestor = new TreeMap<>();

    private int edges;
    private int literals;

    private static boolean add(Map<String, Set<String>> index, String key, String value) {
        Set<String> bucket = index.computeIfAbsent(key, k -> new LinkedHashSet<>());
        return bucket.add(value);
    }

    public void addEdge(String s, String p, String o) {
        if (add(spo, s + US + p, o)) {
            add(pos, p + US + o, s);
            edges += 1;
        }
    }

    public void addType(String s, String cls) {
        addEdge(s, RDF_TYPE, cls);
    }

    public void addLiteral(String s, String p, String val) {
        addLiteral(s, p, val, "xsd:string");
    }

    public void addLiteral(String s, String p, String val, String datatype) {
        if (add(lit, s + US + p, val + US + datatype)) {
            literals += 1;
        }
    }

    public void addSubclassClosure(Iterable<String[]> pairs) {
        for (String[] pair : pairs) {
            String descendant = pair[0];
            String ancestor = pair[1];
            closure.add(descendant + US + ancestor);
            add(descendantsByAncestor, ancestor, descendant);
        }
    }

    public List<String> objects(String s, String p) {
        return sorted(spo.get(s + US + p));
    }

    public List<String> subjects(String p, String o) {
        return sorted(pos.get(p + US + o));
    }

    public List<String> literalValues(String s, String p) {
        Set<String> bucket = lit.get(s + US + p);
        if (bucket == null) {
            return List.of();
        }
        List<String> out = new ArrayList<>();
        for (String entry : bucket) {
            out.add(entry.substring(0, entry.indexOf(US)));
        }
        out.sort(Json::byteCompare);
        return out;
    }

    /** {@code [value, datatype]} pairs, sorted by value then datatype. */
    public List<String[]> literalPairs(String s, String p) {
        Set<String> bucket = lit.get(s + US + p);
        if (bucket == null) {
            return List.of();
        }
        List<String[]> out = new ArrayList<>();
        for (String entry : bucket) {
            int at = entry.indexOf(US);
            out.add(new String[] {entry.substring(0, at), entry.substring(at + 1)});
        }
        out.sort((a, b) -> {
            int c = Json.byteCompare(a[0], b[0]);
            return c != 0 ? c : Json.byteCompare(a[1], b[1]);
        });
        return out;
    }

    public List<String> instancesOf(String cls) {
        Set<String> descendants = descendantsByAncestor.get(cls);
        if (descendants == null) {
            return List.of();
        }
        Set<String> found = new LinkedHashSet<>();
        for (String descendant : descendants) {
            Set<String> subjects = pos.get(RDF_TYPE + US + descendant);
            if (subjects != null) {
                found.addAll(subjects);
            }
        }
        return sorted(found);
    }

    public boolean isA(String node, String cls) {
        Set<String> types = spo.get(node + US + RDF_TYPE);
        if (types == null) {
            return false;
        }
        for (String type : types) {
            if (closure.contains(type + US + cls)) {
                return true;
            }
        }
        return false;
    }

    /** Every triple as a sorted line list, for byte-identity checks against the reference. */
    public List<String> dumpTriples() {
        List<String> lines = new ArrayList<>();
        for (Map.Entry<String, Set<String>> e : spo.entrySet()) {
            int at = e.getKey().indexOf(US);
            String s = e.getKey().substring(0, at);
            String p = e.getKey().substring(at + 1);
            for (String o : e.getValue()) {
                lines.add("E\t" + s + "\t" + p + "\t" + o);
            }
        }
        for (Map.Entry<String, Set<String>> e : lit.entrySet()) {
            int at = e.getKey().indexOf(US);
            String s = e.getKey().substring(0, at);
            String p = e.getKey().substring(at + 1);
            for (String entry : e.getValue()) {
                int a2 = entry.indexOf(US);
                lines.add("L\t" + s + "\t" + p + "\t" + entry.substring(0, a2) + "\t" + entry.substring(a2 + 1));
            }
        }
        for (String pair : closure) {
            int at = pair.indexOf(US);
            lines.add("C\t" + pair.substring(0, at) + "\t" + pair.substring(at + 1));
        }
        lines.sort(Json::byteCompare);
        return lines;
    }

    public int edgeCount() {
        return edges;
    }

    public int literalCount() {
        return literals;
    }

    public int tripleCount() {
        return edges + literals;
    }

    private static List<String> sorted(Set<String> values) {
        if (values == null) {
            return List.of();
        }
        List<String> out = new ArrayList<>(values);
        out.sort(Json::byteCompare);
        return out;
    }
}
