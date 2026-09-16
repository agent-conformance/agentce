# Golden explanation narratives (P3.3)

Decision agentce:event/dec-review
- The AI acted in the role 'assists'. [edge:spiffe://corp/agents/claims-triage]
- It relied on recorded inputs: ci:application-form. [edge:ci:application-form]
- It weighed the options: Escalate to a human, Auto-approve. [edge:agentce:event/dec-review/option/escalate] [edge:agentce:event/dec-review/option/auto]
- It chose 'Escalate to a human'. [edge:agentce:event/dec-review/option/escalate]
- A human reviewed this decision. [edge:agentce:event/appr-review]

Decision agentce:event/dec-approve
- The AI acted in the role 'decides'. [edge:spiffe://corp/agents/claims-triage]
- It relied on recorded inputs: ci:application-form, ci:credit-score. [edge:ci:application-form] [edge:ci:credit-score]
- It weighed the options: Approve the loan, Deny the loan. [edge:agentce:event/dec-approve/option/approve] [edge:agentce:event/dec-approve/option/deny]
- It chose 'Approve the loan'. [edge:agentce:event/dec-approve/option/approve]
- A human principal was accountable for this decision. [edge:agentce:principal/76ec866acba58c9bb5bad99491b00429d834805c30ac5b1ad12cbb178bd243b3]

Decision agentce:event/dec-partial (not reconstructable from record)
- The AI acted in the role 'recommends'. [edge:spiffe://corp/agents/claims-triage]
- It relied on recorded inputs: ci:application-form. [edge:ci:application-form]
- It weighed the options: Approve the loan, Deny the loan. [edge:agentce:event/dec-partial/option/approve] [edge:agentce:event/dec-partial/option/deny]
- The option chosen is not reconstructable from record. [edge:agentce:event/dec-partial]
- Human involvement is not reconstructable from record. [edge:agentce:event/dec-partial]
