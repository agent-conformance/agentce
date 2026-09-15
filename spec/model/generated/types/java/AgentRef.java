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
  The acting agent.
**/
@Data
@EqualsAndHashCode(callSuper=false)
public class AgentRef  {

  private String id;
  private String name;
  private String bundleDigest;


}