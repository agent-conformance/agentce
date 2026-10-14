package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

/**
  Typed references to other events; slot_uri gives the graph relation (SPEC 6.3). A ref names an event by its graph IRI, the event id prefixed with agentce:event/ (SPEC 3). It resolves only to a held event with that IRI. A ref in any other form, a bare event id included, names nothing held, and a control that needs the event it names fails closed. The one exception is decision: it also resolves its padded and bare-id forms (SPEC 10.4) to the held decision, because approval and oversight systems write their own ids and cannot write the engine's IRI.
**/
@Data
@EqualsAndHashCode(callSuper=false)
public class Refs  {

  private String instruction;
  private String authorization;
  private String delegation;
  private String decision;
  private String request;
  private String task;
  private String parent;
  private String origin;
  private String guard;
  private String consumer;
  private String policyDecision;
  private String executedBy;


}