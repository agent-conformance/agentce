export type EvidenceEventId = string;
/**
* Evidence trust class of the emitting system (SPEC 6.4).
*/
export enum SourceClass {
    
    /** Emitted by a system that could have prevented the action. */
    enforcement_point = "enforcement_point",
    /** Emitted by a system the agent's chain cannot transition. */
    independent_system = "independent_system",
    /** Emitted by the agent runtime about itself. */
    self_report = "self_report",
};
/**
* The evidence event types (SPEC 6.2.3).
*/
export enum EventType {
    
    SessionStart = "SessionStart",
    SessionEnd = "SessionEnd",
    BundleLoaded = "BundleLoaded",
    Attestation = "Attestation",
    ModelCall = "ModelCall",
    ToolCall = "ToolCall",
    ResourceAccess = "ResourceAccess",
    MemoryWrite = "MemoryWrite",
    MemoryRead = "MemoryRead",
    Instruction = "Instruction",
    Refusal = "Refusal",
    PolicyDecision = "PolicyDecision",
    AuthzCheck = "AuthzCheck",
    DelegationIssued = "DelegationIssued",
    Decision = "Decision",
    ApprovalRequested = "ApprovalRequested",
    ApprovalDecided = "ApprovalDecided",
    Override = "Override",
    Interrupt = "Interrupt",
    Outcome = "Outcome",
    Incident = "Incident",
    Notice = "Notice",
    Disclosure = "Disclosure",
    IntegrityResult = "IntegrityResult",
};

export enum PrincipalKind {
    
    human = "human",
    service = "service",
    agent = "agent",
};
/**
* Origin class of an instruction (Appendix F).
*/
export enum InstructionSourceClass {
    
    user = "user",
    operator = "operator",
    service = "service",
    agent_identified = "agent_identified",
    agent_unidentified = "agent_unidentified",
    tool_output = "tool_output",
    retrieved = "retrieved",
    memory_trusted = "memory_trusted",
    memory_untrusted = "memory_untrusted",
};

export enum RefusalReasonClass {
    
    policy_conflict = "policy_conflict",
    scope_exceeded = "scope_exceeded",
    unauthorized_principal = "unauthorized_principal",
    untrusted_source = "untrusted_source",
    budget_exceeded = "budget_exceeded",
};

export enum EffectClass {
    
    read = "read",
    write = "write",
    irreversible = "irreversible",
    external_communication = "external_communication",
    spend = "spend",
    physical = "physical",
};

export enum SideEffect {
    
    none = "none",
    read = "read",
    write = "write",
    external = "external",
    irreversible = "irreversible",
};

export enum IntegrityStrength {
    
    source_signed = "source_signed",
    export_anchored = "export_anchored",
    export_chained = "export_chained",
};

export enum IntegrityStatus {
    
    verified = "verified",
    verified_weak = "verified_weak",
    gap = "gap",
    reordered = "reordered",
    unsigned = "unsigned",
    time_suspect = "time_suspect",
    failed = "failed",
};

export enum VerificationStatus {
    
    verified = "verified",
    unverified = "unverified",
    failed = "failed",
};

export enum ToolProtocol {
    
    mcp = "mcp",
    a2a = "a2a",
    http = "http",
    native = "native",
};

export enum PolicyEngine {
    
    opa = "opa",
    cedar = "cedar",
    openfga = "openfga",
    governance_toolkit = "governance_toolkit",
    custom = "custom",
};

export enum PolicyDecisionOutcome {
    
    allow = "allow",
    deny = "deny",
    require_approval = "require_approval",
    transform = "transform",
};

export enum ResourceOperation {
    
    read = "read",
    write = "write",
    delete = "delete",
};

export enum MemoryTrust {
    
    trusted = "trusted",
    untrusted = "untrusted",
    quarantined = "quarantined",
};

export enum GuardVerdict {
    
    allow = "allow",
    sanitize = "sanitize",
    quarantine = "quarantine",
    block = "block",
    review = "review",
};

export enum OutputMarking {
    
    none = "none",
    watermark = "watermark",
    metadata = "metadata",
    label = "label",
};

export enum AiRole {
    
    sole = "sole",
    recommendation = "recommendation",
    assist = "assist",
};

export enum OversightModality {
    
    none = "none",
    review_before = "review_before",
    review_after = "review_after",
    sampled = "sampled",
    dual_control = "dual_control",
    interruptible = "interruptible",
};

export enum EndReason {
    
    completed = "completed",
    interrupted = "interrupted",
    error = "error",
    timeout = "timeout",
};

export enum ApprovalOutcome {
    
    approve = "approve",
    edit = "edit",
    reject = "reject",
};

export enum InterruptMechanism {
    
    stop_button = "stop_button",
    kill_switch = "kill_switch",
    circuit_breaker = "circuit_breaker",
    manual = "manual",
};

export enum InterruptEffect {
    
    halted = "halted",
    paused = "paused",
    degraded = "degraded",
};

export enum IncidentClass {
    
    death_or_health = "death_or_health",
    infrastructure_disruption = "infrastructure_disruption",
    fundamental_rights = "fundamental_rights",
    property_or_environment = "property_or_environment",
};

export enum NoticeType {
    
    subject_of_ai_decision = "subject_of_ai_decision",
    explanation = "explanation",
    adverse_action = "adverse_action",
};

export enum DisclosureType {
    
    ai_interaction = "ai_interaction",
    synthetic_marking = "synthetic_marking",
    deepfake_label = "deepfake_label",
};

export enum ComponentKind {
    
    skill = "skill",
    mcp_server = "mcp_server",
    model = "model",
    prompt = "prompt",
    config = "config",
    policy = "policy",
};

export enum Role {
    
    deployer = "deployer",
    provider = "provider",
    both = "both",
};
/**
* Reasons an event is quarantined at ingest (Appendix F).
*/
export enum QuarantineReason {
    
    schema_invalid = "schema_invalid",
    duplicate_id = "duplicate_id",
    time_order = "time_order",
    unknown_type = "unknown_type",
    unknown_source = "unknown_source",
    class_mismatch = "class_mismatch",
    oversize = "oversize",
    context_mismatch = "context_mismatch",
};


/**
 * The acting agent.
 */
export interface AgentRef {
    /** SPIFFE id or IRI. */
    id: string,
    name?: string,
    /** sha256: digest of the loaded bundle. */
    bundle_digest?: string,
}


/**
 * A human, service, or agent principal.
 */
export interface Principal {
    id: string,
    kind: string,
    role?: string,
    authority_ref?: string,
    org?: string,
}



export interface ModelRef {
    provider?: string,
    name?: string,
    version_or_digest?: string,
}



export interface ToolRef {
    name: string,
    server?: string,
    protocol?: string,
    version_or_digest?: string,
}



export interface Usage {
    input_tokens?: number,
    output_tokens?: number,
}



export interface ResourceRef {
    uri: string,
    kind?: string,
    classification?: string,
    owner?: string,
}



export interface ComponentRef {
    kind?: string,
    name?: string,
    version?: string,
    digest?: string,
    signer?: string,
}



export interface Verification {
    status?: string,
    method?: string,
    log_ref?: string,
}


/**
 * Per-event integrity envelope (SPEC 6.6).
 */
export interface IntegrityBlock {
    hash: string,
    prev: string,
    stream: string,
    strength: string,
    sig_ref?: string,
}


/**
 * Typed references to other event ids; slot_uri gives the graph relation (SPEC 6.3).
 */
export interface Refs {
    /** The instruction this activity acts on. */
    instruction?: string,
    /** The authorizing policy decision. */
    authorization?: string,
    /** The delegation used. */
    delegation?: string,
    /** The decision this activity executes. */
    decision?: string,
    /** The action decided (on authority events). */
    request?: string,
    /** Cross-agent task correlation. */
    task?: string,
    /** The instruction this was derived from. */
    parent?: string,
    /** The activity that produced the content. */
    origin?: string,
    /** The memory guard that ruled on a write. */
    guard?: string,
    /** The consumer of a memory read. */
    consumer?: string,
    /** A policy decision associated with a refusal. */
    policy_decision?: string,
    /** The tool call that executed a decision. */
    executed_by?: string,
}



export interface DecisionOption {
    id: string,
    label?: string,
    refs?: string[],
}


/**
 * CloudEvent envelope carrying an AgentCE evidence payload (SPEC 6.2.1).
 */
export interface EvidenceEvent {
    /** CloudEvents version; MUST be 1.0. */
    specversion: string,
    /** Globally unique event id. */
    id: string,
    /** URI of the emitting system. */
    source: string,
    /** org.agent-conformance.evidence.<EventType>.v1 */
    type: string,
    time: string,
    /** The assessed subject system. */
    subject: string,
    /** MUST be application/ld+json. */
    datacontenttype: string,
    /** W3C Trace Context trace id. */
    agentcetrace?: string,
    /** W3C Trace Context span id. */
    agentcespan?: string,
    /** W3C Trace Context parent id. */
    agentceparent?: string,
    /** Cross-agent task id. */
    agentcetask?: string,
    /** Evidence trust class. */
    agentcesourceclass: string,
    /** Upstream convention version mapped. */
    agentceconv?: string,
    /** JSON-LD payload. */
    data: Payload,
}


/**
 * Common members of every event payload (SPEC 6.2.2).
 */
export interface Payload {
    agent?: AgentRef,
    acted_for?: string[],
    session_id?: string,
    refs?: Refs,
    integrity?: IntegrityBlock,
    /** Source-specific extras; never evaluated by rules. */
    ext?: string,
}



export interface SessionStartPayload extends Payload {
    deployer?: Principal,
    environment?: string,
    bundle_digest?: string,
    model_versions?: string[],
    intended_purpose_ref?: string,
    registration_ref?: string,
}



export interface SessionEndPayload extends Payload {
    end_reason?: string,
}



export interface BundleLoadedPayload extends Payload {
    bundle_digest?: string,
    components?: ComponentRef[],
    attestation_refs?: string[],
}



export interface AttestationPayload extends Payload {
    statement_type?: string,
    subject_digests?: string[],
    signer?: string,
    verification?: Verification,
}



export interface ModelCallPayload extends Payload {
    operation?: string,
    model?: ModelRef,
    usage?: Usage,
    input_ref?: string,
    output_ref?: string,
    output_marking?: string,
    error?: string,
}



export interface ToolCallPayload extends Payload {
    tool?: ToolRef,
    args_ref?: string,
    result_ref?: string,
    side_effect?: string,
    effect_class?: string,
    error?: string,
    used?: string[],
}



export interface ResourceAccessPayload extends Payload {
    resource?: ResourceRef,
    operation?: string,
    purpose?: string,
    count?: number,
}



export interface MemoryWritePayload extends Payload {
    store?: string,
    record_ref?: string,
    provenance_origin_ref?: string,
    provenance_origin_class?: string,
    trust?: string,
    guard_verdict?: string,
}



export interface MemoryReadPayload extends Payload {
    store?: string,
    record_refs?: string[],
    trust_min?: string,
}



export interface InstructionPayload extends Payload {
    instruction_id?: string,
    source_class?: string,
    principal?: Principal,
    content_ref?: string,
}



export interface RefusalPayload extends Payload {
    reason_class?: string,
}



export interface PolicyDecisionPayload extends Payload {
    engine?: string,
    policy_id?: string,
    policy_version?: string,
    policy_digest?: string,
    decision?: string,
    reasons?: string[],
    obligations?: string[],
    principal?: Principal,
}



export interface AuthzCheckPayload extends Payload {
    object?: string,
    relation?: string,
    user?: string,
    allowed?: boolean,
    store_ref?: string,
}



export interface DelegationIssuedPayload extends Payload {
    token_ref?: string,
    issuer?: string,
    subject_principal?: string,
    actor_principal?: string,
    chain?: Principal[],
    scope_granted?: string[],
    scope_parent?: string[],
    expires?: string,
    verification?: Verification,
}



export interface DecisionPayload extends Payload {
    decision_id?: string,
    decision_type?: string,
    affects_natural_person?: boolean,
    legal_or_significant_effect?: boolean,
    ai_role?: string,
    options?: DecisionOption[],
    chosen?: string,
    inputs?: string[],
    rationale_claim_ref?: string,
    oversight_modality?: string,
    person_ref?: string,
}



export interface ApprovalRequestedPayload extends Payload {
    explanation_ref?: string,
    requested_from?: string,
    channel?: string,
    deadline?: string,
}



export interface ApprovalDecidedPayload extends Payload {
    actor?: Principal,
    session_ref?: string,
    outcome?: string,
    edits_ref?: string,
    latency_ms?: number,
    explanation_viewed?: boolean,
}



export interface OverridePayload extends Payload {
    actor?: Principal,
    original?: string,
    replacement?: string,
    reason_code?: string,
}



export interface InterruptPayload extends Payload {
    actor?: Principal,
    mechanism?: string,
    effect?: string,
}



export interface OutcomePayload extends Payload {
    outcome_type?: string,
    observed_at?: string,
    adverse?: boolean,
    reversed?: boolean,
    person_ref?: string,
}



export interface IncidentPayload extends Payload {
    incident_id?: string,
    incident_class?: string,
    detected_at?: string,
    causal_assessment_at?: string,
    provider_notified_at?: string,
    reported_at?: string,
    authority?: string,
    report_ref?: string,
    related_refs?: string[],
}



export interface NoticePayload extends Payload {
    person_ref?: string,
    notice_type?: string,
    delivered_at?: string,
    channel?: string,
    content_ref?: string,
}



export interface DisclosurePayload extends Payload {
    disclosure_type?: string,
    delivered_at?: string,
    mechanism?: string,
}


/**
 * Engine-computed verification outcome per stream (SPEC 6.6).
 */
export interface IntegrityResultPayload extends Payload {
    stream?: string,
    strength?: string,
    status?: string,
    first_bad_index?: number,
    anchors?: string[],
}


