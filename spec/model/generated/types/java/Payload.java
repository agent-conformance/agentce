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
  Common members of every event payload (SPEC 6.2.2).
**/
@Data
@EqualsAndHashCode(callSuper=false)
public abstract class Payload  {

  private AgentRef agent;
  private List<String> actedFor;
  private String sessionId;
  private Refs refs;
  private IntegrityBlock integrity;
  private String ext;


}