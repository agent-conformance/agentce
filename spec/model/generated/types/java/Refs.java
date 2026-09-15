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
  Typed references to other event ids; slot_uri gives the graph relation (SPEC 6.3).
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