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
public class IncidentPayload extends Payload {

  private String incidentId;
  private String incidentClass;
  private ZonedDateTime detectedAt;
  private ZonedDateTime causalAssessmentAt;
  private ZonedDateTime providerNotifiedAt;
  private ZonedDateTime reportedAt;
  private String authority;
  private String reportRef;
  private List<String> relatedRefs;


}