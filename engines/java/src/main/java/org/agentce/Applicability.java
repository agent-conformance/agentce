package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Applicability resolution (SPEC §6.5, §7.3, IR-11). For each subject the resolver derives the
 * enterprise roles, selects the controls that apply, and records why, then reconciles the declared
 * scope against what the evidence shows and raises drift findings. A faithful port of the reference.
 */
public final class Applicability {
    private Applicability() {}

    public static final List<String> ROLES = List.of("deployer", "provider");

    /** The applicability-relevant metadata of a catalog control. */
    public static final class ControlMeta {
        public final String id;
        public final List<String> appliesToRoles;
        public final String family;

        public ControlMeta(String id, List<String> appliesToRoles, String family) {
            this.id = id;
            this.appliesToRoles = appliesToRoles;
            this.family = family;
        }
    }

    /** Expand a declared role to concrete roles ({@code both} -> deployer and provider), per IR-11. */
    public static List<String> effectiveRoles(String role) {
        if ("both".equals(role)) {
            return new ArrayList<>(ROLES);
        }
        if ("deployer".equals(role) || "provider".equals(role)) {
            return List.of(role);
        }
        return List.of("deployer"); // the conservative default
    }

    private static boolean roleApplies(Set<String> subjectRoles, List<String> appliesToRoles) {
        Set<String> targets = new LinkedHashSet<>(appliesToRoles);
        if (targets.contains("both")) {
            targets.addAll(ROLES);
        }
        for (String r : subjectRoles) {
            if (targets.contains(r)) {
                return true;
            }
        }
        return false;
    }

    private static Set<String> expanded(List<String> appliesToRoles) {
        Set<String> targets = new LinkedHashSet<>(appliesToRoles);
        if (targets.contains("both")) {
            targets.addAll(ROLES);
        }
        Set<String> out = new LinkedHashSet<>();
        for (String r : targets) {
            if (ROLES.contains(r)) {
                out.add(r);
            }
        }
        return out;
    }

    private static String eventType(JsonNode event) {
        JsonNode data = event.get("data");
        if (data != null && data.isObject() && data.get("@type") != null && data.get("@type").isTextual()) {
            return data.get("@type").textValue();
        }
        return "";
    }

    private static String subjectOf(JsonNode event) {
        JsonNode s = event.get("subject");
        return s != null && s.isTextual() ? s.textValue() : "";
    }

    private static List<String> observedDecisionTypes(String subjectId, List<JsonNode> events) {
        Set<String> observed = new LinkedHashSet<>();
        for (JsonNode event : events) {
            if (!subjectOf(event).equals(subjectId) || !eventType(event).equals("Decision")) {
                continue;
            }
            JsonNode data = event.get("data");
            if (data != null && data.isObject() && data.get("decision_type") != null && data.get("decision_type").isTextual()) {
                observed.add(data.get("decision_type").textValue());
            }
        }
        List<String> out = new ArrayList<>(observed);
        out.sort(Json::byteCompare);
        return out;
    }

    private static Set<String> observedComponents(String subjectId, List<JsonNode> events) {
        Set<String> observed = new LinkedHashSet<>();
        for (JsonNode event : events) {
            if (!subjectOf(event).equals(subjectId)) {
                continue;
            }
            JsonNode data = event.get("data");
            if (data == null || !data.isObject()) {
                continue;
            }
            if (eventType(event).equals("Component")) {
                for (String key : new String[] {"name", "id"}) {
                    if (data.get(key) != null && data.get(key).isTextual()) {
                        observed.add(data.get(key).textValue());
                    }
                }
            }
            if (eventType(event).equals("BundleLoaded")) {
                JsonNode components = data.get("components");
                if (components != null && components.isArray()) {
                    for (JsonNode component : components) {
                        if (component.isTextual()) {
                            observed.add(component.textValue());
                        } else if (component.isObject() && component.get("name") != null && component.get("name").isTextual()) {
                            observed.add(component.get("name").textValue());
                        }
                    }
                }
            }
        }
        return observed;
    }

    private static ArrayNode classJustifications(Profile.Subject subject) {
        ArrayNode out = Json.nodes().arrayNode();
        for (Profile.EvidenceSource source : subject.evidenceSources) {
            if (source.cls != null && !source.cls.isEmpty()) {
                ObjectNode row = out.addObject();
                row.put("source", source.source);
                row.put("class", source.cls);
                row.put("justification", "declared for " + source.source
                        + " in the applicability profile of " + subject.id);
            }
        }
        return out;
    }

    private static ObjectNode resolveSubject(
            Profile.Subject subject, List<JsonNode> events, List<ControlMeta> controls, List<String> catalogs) {
        List<String> roles = effectiveRoles(subject.role);
        Set<String> roleSet = new LinkedHashSet<>(roles);

        List<ControlMeta> sortedControls = new ArrayList<>(controls);
        sortedControls.sort((a, b) -> Json.byteCompare(a.id, b.id));
        ArrayNode controlRows = Json.nodes().arrayNode();
        for (ControlMeta control : sortedControls) {
            boolean applicable = roleApplies(roleSet, control.appliesToRoles);
            Set<String> inBoth = expanded(control.appliesToRoles);
            ObjectNode row = controlRows.addObject();
            row.put("id", control.id);
            row.put("applicable", applicable);
            row.put("reason_code", applicable ? "role_match" : "role_mismatch");
            ArrayNode refs = row.putArray("reason_refs");
            List<String> matched = new ArrayList<>();
            for (String r : roleSet) {
                if (inBoth.contains(r)) {
                    matched.add(r);
                }
            }
            matched.sort(Json::byteCompare);
            matched.forEach(refs::add);
        }

        List<String> observed = observedDecisionTypes(subject.id, events);
        Set<String> declaredSet = new LinkedHashSet<>(subject.declaredDecisionTypes);
        List<String> declared = new ArrayList<>(declaredSet);
        declared.sort(Json::byteCompare);
        ArrayNode drift = Json.nodes().arrayNode();
        for (String decisionType : observed) {
            if (!declared.contains(decisionType)) {
                drift.addObject().put("kind", "undeclared_decision_type").put("ref", decisionType);
            }
        }
        Set<String> declaredComponents = new LinkedHashSet<>(subject.declaredComponents);
        List<String> extraComponents = new ArrayList<>();
        for (String c : observedComponents(subject.id, events)) {
            if (!declaredComponents.contains(c)) {
                extraComponents.add(c);
            }
        }
        extraComponents.sort(Json::byteCompare);
        for (String component : extraComponents) {
            drift.addObject().put("kind", "undeclared_component").put("ref", component);
        }

        ObjectNode statement = Json.nodes().objectNode();
        statement.put("subject", subject.id);
        ArrayNode rolesArr = statement.putArray("roles");
        roles.forEach(rolesArr::add);
        ArrayNode catalogsArr = statement.putArray("catalogs");
        catalogs.forEach(catalogsArr::add);
        statement.set("controls", controlRows);
        ArrayNode observedArr = statement.putArray("observed_decision_types");
        observed.forEach(observedArr::add);
        ArrayNode declaredArr = statement.putArray("declared_decision_types");
        declared.forEach(declaredArr::add);
        statement.set("drift", drift);
        statement.set("class_justifications", classJustifications(subject));
        return statement;
    }

    /** Resolve one applicability statement per declared subject. */
    public static ArrayNode resolve(Profile profile, List<JsonNode> events, List<ControlMeta> controls) {
        ArrayNode statements = Json.nodes().arrayNode();
        List<ObjectNode> statementList = new ArrayList<>();
        for (Profile.Subject s : profile.subjects) {
            ObjectNode statement = resolveSubject(s, events, controls, profile.catalogs);
            statements.add(statement);
            statementList.add(statement);
        }

        Set<String> declaredIds = new LinkedHashSet<>();
        for (Profile.Subject s : profile.subjects) {
            declaredIds.add(s.id);
        }
        Set<String> observedSubjects = new LinkedHashSet<>();
        for (JsonNode e : events) {
            String s = subjectOf(e);
            if (!s.isEmpty()) {
                observedSubjects.add(s);
            }
        }
        List<String> undeclared = new ArrayList<>();
        for (String s : observedSubjects) {
            if (!declaredIds.contains(s)) {
                undeclared.add(s);
            }
        }
        undeclared.sort(Json::byteCompare);
        if (!undeclared.isEmpty() && !statementList.isEmpty()) {
            ArrayNode drift = (ArrayNode) statementList.get(0).get("drift");
            for (String s : undeclared) {
                drift.addObject().put("kind", "undeclared_subject").put("ref", s);
            }
        }
        return statements;
    }
}
