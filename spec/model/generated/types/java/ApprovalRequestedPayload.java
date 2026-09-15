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
public class ApprovalRequestedPayload extends Payload {

  private String explanationRef;
  private String requestedFrom;
  private String channel;
  private ZonedDateTime deadline;


}