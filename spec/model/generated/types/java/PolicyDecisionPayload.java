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
public class PolicyDecisionPayload extends Payload {

  private String engine;
  private String policyId;
  private String policyVersion;
  private String policyDigest;
  private String decision;
  private List<String> reasons;
  private List<String> obligations;
  private Principal principal;


}