package example;

/* metamodel_version: 1.11.0 */
/* version: 0.1.0 */
import java.net.URI;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.util.List;
import lombok.*;

@Data
@EqualsAndHashCode(callSuper=false)
public class ApprovalDecidedPayload extends Payload {

  private Principal actor;
  private String sessionRef;
  private String outcome;
  private String editsRef;
  private Integer latencyMs;
  private Boolean explanationViewed;


}