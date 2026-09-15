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
public class OutcomePayload extends Payload {

  private String outcomeType;
  private ZonedDateTime observedAt;
  private Boolean adverse;
  private Boolean reversed;
  private String personRef;


}